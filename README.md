# WINE Citra

WINE Citra is a desktop launcher and prefix manager for running Windows
programs with Wine on Linux. It provides a simple executable picker, per-app
logs, graphics and audio controls, and tools for managing Wine prefixes.

## Requirements

- Linux with a graphical desktop session
- Python 3 and PyQt6
- Wine
- FFmpeg for clip capture and clip previews
- python-xlib for fullscreen controls and cursor trapping

Winetricks, GameMode, PulseAudio/PipeWire utilities, and Vulkan drivers are
optional and are used when available.

## Run from source

```sh
cd Application
python3 main.py
```

In the **Launcher** tab, choose or drop a Windows `.exe` and select **Launch**.
Less common arguments and prefix overrides are grouped under **Advanced launch
options**. The Settings tab includes Wine, graphics, audio, display, launcher,
and accessibility preferences. Running programs can be stopped or their logs
opened from the launcher.

The launcher supports keyboard use: **Ctrl+O** opens the executable picker,
**Ctrl+Enter** launches the selected program, and **F5** refreshes the app's
lists. Press **F12** while a Wine program is focused to save a screenshot and
show a short preview. Screenshot capture, its save folder, preview corner, and
display time are configurable under **Settings > Capture**. Double-click a
running program in the Launcher list to bring its window forward; use **Open
Log** for its log.
With a managed Wine program running, **F8** opens the launcher or focuses its
existing window. **Ctrl+F12** saves a recent game clip. Clip capture requires
FFmpeg, records the first Wine window, and its duration (5 seconds to 5
minutes) is configurable under **Settings > Capture**. It needs the selected
clip length to fill its rolling buffer after launch or a window-size change.
Capture restarts at the new game-window dimensions when entering or leaving
fullscreen, preventing a stale-sized crop. Game audio is captured from
the selected output device's PulseAudio/PipeWire monitor when available; clips
and their save folder are configurable in Settings. A short silent animated
preview is enabled by default and starts as soon as it is rendered while the
notification is already visible; it can be disabled in Settings. Capture
notifications are click-through and do not intercept game input. The
**Clips** tab shows video-frame previews of saved clips and opens them in the
desktop media player. Press **F8** while a screenshot or clip notification is
visible to open WINE Citra directly to its matching gallery. Screenshots and
clips can be renamed or deleted from their gallery tabs.
The **Screenshots** tab displays saved captures in a gallery; Ctrl/Shift-click
to select multiple items and delete them together. The **Clips** tab supports
the same multi-select deletion workflow. Screenshot notifications slide in
from the right and out to the right; their size,
position, duration, animation speed, and notification sound volume can be
adjusted and previewed under **Settings > Capture**. Error/crash sounds have
separate enable and volume controls. Sound playback uses `paplay` or `ffplay`
when available.

The **Report** tab is focused on anonymous bug submissions and optional private
screenshots and clip attachments. Submitted reports can be reviewed in the
Supabase inbox described below; the app does not keep a local report list.
There are no suggestions, statuses, or conversation features.
Maintainer sign-in and administration are not part of the end-user app. To
enable reports, run `Application/Database/supabase_reports.sql` (or
`/usr/lib/wine-citra/Database/supabase_reports.sql` after package install) in
the Supabase SQL Editor and enable anonymous sign-ins under Authentication.
Attachments are stored in a private bucket and limited to 50 MB each and 100 MB
total per report. Anonymous report sessions are stored locally with owner-only
file permissions. Re-running the updated SQL removes the old report status
column while preserving report content and any legacy conversation data.

The app uploads attachment bytes to Supabase Storage; the database stores their
names and private object paths. For a readable one-row-per-report inbox, run this in Supabase **SQL Editor**
or add it as a dashboard block:

```sql
select report_id, submitted_at, title, description, attachments
from public.report_inbox
order by submitted_at desc;
```

The migration creates this read-only view for dashboard use and does not grant
the app or anonymous users access to other reporters' submissions. To locate
actual uploaded files, query `public.report_inbox_files` for their storage
paths, then open **Storage → report-attachments** to preview or download them.
Run the updated SQL file once to add report IDs to both views.
To remove a report, first find its attachment paths:

```sql
select storage_path
from public.report_inbox_files
where report_id = 'REPORT-UUID';
```

Delete those objects from **Storage → report-attachments**, then delete the
report and its attachment metadata from the SQL Editor:

```sql
delete from public.reports
where id = 'REPORT-UUID';
```

Deleting the database row cascades to its attachment metadata, but does not
delete the actual files from Storage; remove those first to avoid orphaned
uploads.

Keep project settings out of Git by creating
`~/.config/wine-citra/supabase.json` on the machine running the app:

```json
{
  "url": "https://YOUR-PROJECT.supabase.co",
  "publishable_key": "sb_publishable_YOUR-KEY"
}
```

Restrict the local settings file with `chmod 600 ~/.config/wine-citra/supabase.json`.
The app also accepts `WINE_CITRA_SUPABASE_URL` and `WINE_CITRA_SUPABASE_KEY`
environment variables. The publishable key is intended for client use; never
put database passwords or service-role keys in this file or the application.
Optional vkBasalt FXAA/SMAA post-processing requires the vkBasalt Vulkan layer;
**Home** toggles its effects in game. The interface text size can also be
increased in Settings.

The Updates tab lists stable releases (and optional beta versions) in a
version selector, with release notes and package links shown on a card.
WINE Citra uses `Application/Assets/windows_icon.svg` for its app and desktop
icons; the packager installs it into the system icon theme.

## Data locations

- Settings: `~/.config/wine-citra/settings.json`
- Prefixes and logs: `~/.local/share/wine-citra/`
- Anonymous report identity: `~/.local/share/wine-citra/report-session.json`

Keep the report session file if you want to continue accessing reports created
from this installation. It contains a local refresh token and is restricted to
the current user.

WINE Citra does not bundle Wine or Windows programs.

## Support

https://ko-fi.com/blitzzdragon
