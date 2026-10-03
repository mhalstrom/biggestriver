"""The tests never reach the tmux of the person who runs them. With no tmux found, launch_in auto is a
Terminal tab, which each test fakes (server.TERMINAL_RUNNER); a tmux command needs the fake tmux
(server.TMUX_RUNNER) or a tmux server of its own (server.TMUX_CMD). A test of the tmux default sets
core.tmux_path itself."""
from river import core

core.tmux_path = lambda: None
