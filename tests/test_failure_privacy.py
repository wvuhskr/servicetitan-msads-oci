import json
from pathlib import Path
from st_msads_oci.cli import main


def config(tmp_path):
    path = tmp_path / 'accounts.yaml'
    path.write_text('servicetitan: {campaign_category: "Paid Microsoft", tenant_id: "1"}\n')
    return ['--config', str(path), '--project-dir', str(tmp_path)]


def test_build_validation_failure_alert_and_console_do_not_expose_customer(monkeypatch, tmp_path, capsys):
    root = Path(__file__).parents[1]
    payload = json.loads((root / 'examples/sample-input.json').read_text())
    payload['jobs'][0]['email'] = ['private@example.com']
    infile, mapfile = tmp_path / 'input.json', tmp_path / 'map.json'
    infile.write_text(json.dumps(payload)); mapfile.write_text('{"ids":{},"dni":{},"forms":[]}')
    monkeypatch.setenv('OCI_MAP_FILE', str(mapfile))
    monkeypatch.setenv('OCI_DOWNLOADS_DIR', str(tmp_path / 'none'))
    assert main(['build', *config(tmp_path), '--input', str(infile)]) == 2
    output = capsys.readouterr()
    assert 'private@example.com' not in output.out + output.err
    assert 'email' in output.out + output.err


def test_arbitrary_build_exception_does_not_echo_secrets_to_alerts(monkeypatch, tmp_path, capsys):
    def failed_build(*args, **kwargs):
        raise RuntimeError('https://hooks.example/SUPER_SECRET private@example.com')
    monkeypatch.setattr('st_msads_oci.cli.build', failed_build)
    assert main(['build', *config(tmp_path), '--input', 'unused']) == 2
    output = capsys.readouterr()
    assert 'SUPER_SECRET' not in output.out + output.err
    assert 'private@example.com' not in output.out + output.err
    assert 'RuntimeError' in output.out + output.err


def test_notifier_error_details_are_not_serialized_in_summary():
    from st_msads_oci.notify import notify_all
    class FailingNotifier:
        def send(self, subject, body):
            raise OSError('credential=SUPER_SECRET private@example.com')
    errors = notify_all([FailingNotifier()], 's', 'b')
    assert len(errors) == 1 and 'OSError' in errors[0]
    assert 'SUPER_SECRET' not in errors[0] and 'private@example.com' not in errors[0]


def test_pull_notification_preference_and_error_privacy(monkeypatch, tmp_path, capsys):
    from st_msads_oci import notify
    from st_msads_oci.pull import servicetitan
    for key in ['ST_CLIENT_ID', 'ST_CLIENT_SECRET', 'ST_APP_KEY']:
        monkeypatch.setenv(key, 'fake-test-value')
    def fail_pull(*args, **kwargs):
        raise RuntimeError('SUPER_SECRET private@example.com')
    monkeypatch.setattr(servicetitan, 'pull_all', fail_pull)
    sent = []
    class CapturedNotification:
        def send(self, subject, body): sent.append((subject, body))
    # Construction now goes through notify.safe_build_notifiers -> notify.build_notifiers;
    # patch the real seam so the wrapper is exercised too.
    monkeypatch.setattr(notify, 'build_notifiers', lambda *a: [CapturedNotification()])
    args = ['pull', *config(tmp_path)]
    assert main([*args, '--no-notify']) == 2
    assert sent == []
    assert main(args) == 2
    assert len(sent) == 1
    console = capsys.readouterr()
    for sensitive in ['SUPER_SECRET', 'private@example.com']:
        assert sensitive not in str(sent) + console.out + console.err
