# Records docs/demo/demo.gif: `make demo-gif` (needs vhs, ttyd and ffmpeg; the database must be up).
# The demo needs no model: see evals/demo.py.
Output docs/demo/demo.gif

Set Shell "bash"
Set FontSize 15
Set Width 1280
Set Height 720
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set PlaybackSpeed 1
Set Framerate 12

Hide
Type "export PS1='$ ' && clear"
Enter
Show

Sleep 700ms
Type "uv run python -m evals.demo --act 1"
Sleep 400ms
Enter
Sleep 11s

Type "clear"
Enter
Type "uv run python -m evals.demo --act 2"
Sleep 400ms
Enter
Sleep 10s

Type "clear"
Enter
Type "uv run python -m evals.demo --act 3"
Sleep 400ms
Enter
Sleep 9s
