# Clear companion activity and stable request UI

User wants screenshot/request behavior reviewed, less UI blinking, flexible layout,
and an obvious thinking state rather than eye/mouth changes. User chose cursor
following by default with optional corner placement. Existing Python/PySide6
architecture and minimal companion/pill remain. Implementation choices below are
assistant defaults within the requested scope.

Approaches: a separate dashboard would add surface area and capture complexity;
trusting Windows capture exclusion alone would weaken existing safeguards.
Instead extend the existing companion with a small labeled badge and state-specific
color/shape cues, keeping both existing windows inside capture isolation. Thinking
gets a purple activity ring; acting, review, success and error have distinct text.
Reduced motion uses a static ring, no eye blinking or bobbing. A busy indicator
means a request is pending, not that hidden reasoning is available or enabled.

Add 75–150% size and cursor/top-right/bottom-right primary-display placement in
existing settings. Cursor following remains the default. Status UI is passive and
never takes focus. Model settings connection/vision checks surface their activity
on the companion until completion, cancellation or close, then restore task state.

Screenshot review: actual desktop requests hide both overlays, wait for compositor
settling, capture foreground display physical pixels, restore without activation,
then submit PNG. Synthetic compatibility checks send generated pixels only, not
screenshots. Preserve conservative hide/capture/restore; do not claim zero capture
flicker or native qualification. Reduce unnecessary pill HWND recreation by
applying final interaction flags once, including passive approval, rather than
switching through intermediate focus modes. Preserve stop/dismiss restoration
suppression. No screenshots, instructions or reasoning are persisted by this UI.

Model settings use a scrollable form with request/status/save controls outside the
scroll area; expanding advanced options must not grow the dialog off-screen. Task
stage labels distinguish capture, compatibility, planning, dispatch and visual
verification without changing input policy. These are implementation defaults to
fulfil flexible GUI and request-execution visibility requirements.
