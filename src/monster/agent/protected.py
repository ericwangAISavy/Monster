"""Apps that drive Monster are off limits for its own input unless the task names them.

Monster is usually commanded from an editor or chat app (Claude Code in VS Code, ChatGPT).
A misplaced click or keystroke there could type into the very prompt that controls it,
so input aimed at those windows is refused before dispatch.
"""

PROTECTED_APPS = {
    "code.exe": "Visual Studio Code",
    "code - insiders.exe": "Visual Studio Code",
    "cursor.exe": "Cursor",
    "claude.exe": "Claude",
    "chatgpt.exe": "ChatGPT",
}
# Words that, in the task, mean the user really wants Monster to work in that app.
NAMED = {
    "Visual Studio Code": ("visual studio code", "vs code", "vscode"),
    "Cursor": ("cursor",),
    "Claude": ("claude",),
    "ChatGPT": ("chatgpt",),
}


def protected_target(action, foreground, task, *, window_at, process_name):
    """Name of the protected app this physical action would land in, or None."""
    if action.get("op") == "press" and action.get("key") == "WIN":
        return None  # Opening Start is how Monster leaves the controlling app.
    if "x" in action:
        handles = [window_at(action["x"], action["y"])]
        if "end_x" in action:
            handles.append(window_at(action["end_x"], action["end_y"]))
    else:
        handles = [foreground]
    text = (task or "").lower()
    for handle in handles:
        app = PROTECTED_APPS.get((process_name(handle) or "").lower())
        if app and not any(word in text for word in NAMED[app]):
            return app
    return None


def native_guard():
    """The real-desktop guard used by the app; tests leave the controller without one."""
    from monster.native import root_window_at, window_process_name

    def guard(action, foreground, task):
        return protected_target(action, foreground, task, window_at=root_window_at,
                                process_name=window_process_name)

    return guard
