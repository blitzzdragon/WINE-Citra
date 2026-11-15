"""Keep a rolling video buffer of a Wine window and save recent clips."""

import datetime
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from Core.Runtime.application_runtime import DATA_DIR, log

DEFAULT_CLIP_SECONDS = 30
MIN_CLIP_SECONDS = 5
MAX_CLIP_SECONDS = 300
BUFFER_SEGMENT_OVERHEAD = 6


class RollingClipRecorder:
    def __init__(self):
        self.process = None
        self.window_id = None
        self._temporary_directory = None
        self._log_handle = None
        self.audio_enabled = True
        self.audio_output = ""
        self.capture_geometry = None
        self.clip_duration = DEFAULT_CLIP_SECONDS
        self.buffer_segments = DEFAULT_CLIP_SECONDS + BUFFER_SEGMENT_OVERHEAD

    def start(self, window_id, audio_enabled=True, audio_output="",
              capture_geometry=None, clip_duration=DEFAULT_CLIP_SECONDS):
        self.stop()
        try:
            clip_duration = int(clip_duration)
        except (TypeError, ValueError, OverflowError):
            log(f"Clip recording skipped invalid duration: {clip_duration!r}.")
            return False
        if not MIN_CLIP_SECONDS <= clip_duration <= MAX_CLIP_SECONDS:
            log(f"Clip recording skipped invalid duration: {clip_duration}.")
            return False
        ffmpeg = shutil.which("ffmpeg")
        display = os.environ.get("DISPLAY")
        if not ffmpeg:
            log("Clip recording unavailable: ffmpeg is not installed.")
            return False
        if not display:
            log("Clip recording unavailable: no X11 display is configured.")
            return False

        buffer_segments = clip_duration + BUFFER_SEGMENT_OVERHEAD
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._temporary_directory = tempfile.TemporaryDirectory(
                prefix="wine-citra-clip-buffer-", dir=DATA_DIR)
            buffer_dir = Path(self._temporary_directory.name)
            segment_pattern = buffer_dir / "segment-%03d.ts"
            self._log_handle = open(
                DATA_DIR / "clip-recorder.log", "a", encoding="utf-8")
        except OSError as error:
            log(f"Could not prepare clip recording: {error}")
            self._cleanup()
            return False
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-f", "x11grab", "-framerate", "30", "-draw_mouse", "1",
        ]
        if capture_geometry:
            x, y, width, height = (int(value) for value in capture_geometry)
            if width < 2 or height < 2:
                log(f"Clip recording skipped invalid capture geometry: "
                    f"{capture_geometry}.")
                self._cleanup()
                return False
            command.extend(["-video_size", f"{width}x{height}", "-i",
                            f"{display}+{x},{y}"])
            self.capture_geometry = (x, y, width, height)
        else:
            command.extend([
                "-window_id", str(int(window_id)), "-i", display,
            ])
            self.capture_geometry = None
        monitor_source = (self._pulse_monitor_source(audio_output)
                          if audio_enabled else None)
        if monitor_source:
            command.extend(["-thread_queue_size", "512", "-f", "pulse",
                            "-i", monitor_source])
        else:
            command.append("-an")
            if audio_enabled:
                log("No PulseAudio/PipeWire output monitor was found; "
                    "recording clips without audio.")
        command.extend([
            "-map", "0:v:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune",
            "zerolatency", "-pix_fmt", "yuv420p", "-g", "30",
            "-keyint_min", "30", "-sc_threshold", "0",
            "-force_key_frames", "expr:gte(t,n_forced*1)",
        ])
        if monitor_source:
            command.extend(["-map", "1:a:0", "-c:a", "aac",
                            "-b:a", "128k"])
        command.extend([
            "-f", "segment", "-segment_time", "1",
            "-segment_wrap", str(buffer_segments),
            "-reset_timestamps", "1", "-segment_format", "mpegts",
            str(segment_pattern),
        ])
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=self._log_handle,
                start_new_session=True)
        except OSError as error:
            log(f"Could not start clip recording: {error}")
            self._cleanup()
            return False

        self.window_id = int(window_id)
        self.audio_enabled = bool(audio_enabled)
        self.audio_output = str(audio_output or "")
        self.clip_duration = clip_duration
        self.buffer_segments = buffer_segments
        if self.capture_geometry:
            x, y, width, height = self.capture_geometry
            log(f"Started rolling clip buffer for Wine window "
                f"0x{self.window_id:x} at {width}x{height}+{x}+{y} "
                f"(pid {self.process.pid}).")
        else:
            log(f"Started rolling clip buffer for Wine window "
                f"0x{self.window_id:x} (pid {self.process.pid}).")
        return True

    @staticmethod
    def _pulse_monitor_source(audio_output):
        pactl = shutil.which("pactl")
        if not pactl:
            return None
        sink = str(audio_output or "").strip()
        if not sink:
            try:
                result = subprocess.run(
                    [pactl, "get-default-sink"], capture_output=True,
                    text=True, timeout=3, check=False)
            except (OSError, subprocess.SubprocessError):
                return None
            if result.returncode:
                return None
            sink = result.stdout.strip()
        if not sink:
            return None

        wanted = f"{sink}.monitor"
        try:
            result = subprocess.run(
                [pactl, "list", "sources", "short"],
                capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode:
            return None
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) > 1 and fields[1] == wanted:
                return wanted
        return None

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def save_recent(self, directory):
        if not self.is_running() or self._temporary_directory is None:
            return None, "Clip recording is not running."

        buffer_dir = Path(self._temporary_directory.name)
        segments = sorted(
            (path for path in buffer_dir.glob("segment-*.ts")
             if path.is_file()),
            key=lambda path: path.stat().st_mtime_ns,
        )
        if len(segments) < self.clip_duration + 1:
            captured = max(0, len(segments) - 1)
            return None, (
                f"Clip buffer is warming up ({captured}/"
                f"{self.clip_duration} seconds captured).")

        # FFmpeg leaves the segment it is currently writing in the folder.
        # Exclude it so the saved clip contains only complete segments.
        segments = segments[:-1]
        directory = Path(directory).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        output = directory / f"clip-{timestamp}.mp4"
        manifest = buffer_dir / f"concat-{timestamp}.txt"
        chosen = segments[-self.clip_duration:]
        try:
            with manifest.open("w", encoding="utf-8") as handle:
                for segment in chosen:
                    escaped = str(segment.resolve()).replace("'", "'\\''")
                    handle.write(f"file '{escaped}'\n")
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                return None, "Clip recording unavailable: ffmpeg is not installed."
            result = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-y", "-f", "concat", "-safe", "0", "-i", str(manifest),
                    "-c", "copy", "-movflags", "+faststart", str(output),
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, timeout=60, check=False)
            if result.returncode:
                output.unlink(missing_ok=True)
                detail = result.stderr.strip() or "FFmpeg could not join segments."
                return None, detail
        except (OSError, subprocess.SubprocessError) as error:
            output.unlink(missing_ok=True)
            return None, f"Could not save clip: {error}"
        finally:
            manifest.unlink(missing_ok=True)

        log(f"Saved {self.clip_duration}-second clip: {output}")
        return output, None

    @staticmethod
    def create_preview(clip_path, clip_duration=DEFAULT_CLIP_SECONDS):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None, "FFmpeg is not installed."
        try:
            clip_duration = int(clip_duration)
        except (TypeError, ValueError, OverflowError):
            return None, "The configured clip duration is invalid."
        if not MIN_CLIP_SECONDS <= clip_duration <= MAX_CLIP_SECONDS:
            return None, "The configured clip duration is out of range."
        preview_duration = min(4, clip_duration)
        preview_start = max(0, clip_duration - preview_duration)
        descriptor, preview_name = tempfile.mkstemp(
            prefix="wine-citra-clip-preview-", suffix=".gif")
        os.close(descriptor)
        preview = Path(preview_name)
        try:
            result = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-y", "-ss", str(preview_start), "-i", str(clip_path),
                    "-t", str(preview_duration),
                    "-an", "-vf",
                    "fps=10,scale=320:-2:flags=lanczos,split[s0][s1];"
                    "[s0]palettegen=max_colors=96[p];"
                    "[s1][p]paletteuse=dither=bayer",
                    "-loop", "0", str(preview),
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, timeout=10, check=False)
            if result.returncode or not preview.is_file() \
                    or preview.stat().st_size == 0:
                preview.unlink(missing_ok=True)
                detail = result.stderr.strip() or "FFmpeg could not create the preview."
                return None, detail
        except (OSError, subprocess.SubprocessError) as error:
            preview.unlink(missing_ok=True)
            return None, f"Could not create animated preview: {error}"
        return preview, None

    def stop(self):
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            try:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            except OSError:
                pass
        if self.window_id is not None:
            log(f"Stopped rolling clip buffer for Wine window "
                f"0x{self.window_id:x}.")
        self.window_id = None
        self.audio_enabled = False
        self.audio_output = ""
        self.capture_geometry = None
        self._cleanup()

    def _cleanup(self):
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None
