"""Safety checks for the study environment runner, with no cluster or API."""

import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'study' / 'local-case.sh'


def test_setup_rejects_a_different_cluster_context(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    marker = tmp_path / 'accessed-cluster'
    kubectl = bin_dir / 'kubectl'
    kubectl.write_text(
        '#!/bin/bash\n'
        'if [ "$1 $2" = "config current-context" ]; then\n'
        '  echo production\n'
        'else\n'
        f'  touch "{marker}"\n'
        'fi\n'
    )
    kubectl.chmod(0o755)
    env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}', HOLMES_STUDY_STATE_DIR=str(tmp_path / 'state'))
    result = subprocess.run(['bash', str(SCRIPT), 'setup'], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert 'Refusing to operate' in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize('has_template', [False, True])
def test_eval_without_credentials_does_not_access_cluster_or_model(tmp_path, has_template):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    marker = tmp_path / 'external-command'
    for command in ('kubectl', 'poetry'):
        stub = bin_dir / command
        stub.write_text(f'#!/bin/bash\ntouch "{marker}"\nexit 99\n')
        stub.chmod(0o755)
    state = tmp_path / 'state'
    if has_template:
        state.mkdir()
        (state / 'deepseek.env').write_text('DEEPSEEK_BASE_URL=""\nDEEPSEEK_MODEL_ID=""\nDEEPSEEK_API_KEY=""\n')
    env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}', HOLMES_STUDY_STATE_DIR=str(state))
    result = subprocess.run(['bash', str(SCRIPT), 'eval'], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert 'Fill ' in result.stderr
    assert not marker.exists()
