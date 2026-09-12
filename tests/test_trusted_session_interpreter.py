import sys
from tools import run_trusted_llm_session as runner


def test_entry_always_dispatches_selected_environment(monkeypatch, tmp_path):
    selected = tmp_path/'venv-python'
    selected.symlink_to(sys.executable)
    monkeypatch.setattr(runner, 'interpreter_for', lambda route: str(selected))
    monkeypatch.setattr(sys, 'argv', ['runner', '--route', 'd3_ev2gym_electric_competition',
        '--query', 'test', '--example-action', '{}', '--output-dir', str(tmp_path/'output')])
    commands = []
    monkeypatch.setattr(runner.subprocess, 'call', lambda command: commands.append(command) or 17)
    assert runner.main() == 17
    assert commands[0][0] == str(selected)
    assert commands[0][-1] == '--worker'
    assert not (tmp_path/'output').exists()
