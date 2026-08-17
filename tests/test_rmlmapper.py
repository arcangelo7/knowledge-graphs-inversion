# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import subprocess

from conformance import rmlmapper


def test_execute_returns_process_output(monkeypatch, tmp_path):
    mapping_path = tmp_path / "mapping.ttl"
    output_path = tmp_path / "output.nq"
    expected_command = [
        "java",
        "-Xmx1g",
        "-jar",
        "/opt/rmlmapper.jar",
        "-m",
        str(mapping_path),
        "-s",
        "nquads",
        "-o",
        str(output_path),
        "-dsn",
        "jdbc:postgresql://postgres/db",
        "-u",
        "user",
        "-p",
        "password",
    ]
    expected_result = subprocess.CompletedProcess(
        expected_command,
        1,
        stdout="mapper stdout\n",
        stderr="mapper stderr\n",
    )

    def execute_process(command, capture_output, text, timeout):
        assert command == expected_command
        assert capture_output is True
        assert text is True
        assert timeout == 60
        return expected_result

    monkeypatch.setattr(rmlmapper, "_get_jar_path", lambda: "/opt/rmlmapper.jar")
    monkeypatch.setattr(subprocess, "run", execute_process)

    result = rmlmapper.execute(
        str(mapping_path),
        str(output_path),
        dsn="jdbc:postgresql://postgres/db",
        username="user",
        password="password",
        timeout=60,
        java_options=("-Xmx1g",),
    )

    assert result == expected_result


def test_run_returns_exit_code_and_prints_failed_process_output(monkeypatch, capsys):
    process = subprocess.CompletedProcess(
        ["java"],
        1,
        stdout="mapper stdout\n",
        stderr="mapper stderr\n",
    )
    monkeypatch.setattr(rmlmapper, "execute", lambda *args, **kwargs: process)

    result = rmlmapper.run("mapping.ttl", "output.nq")

    assert result == 1
    assert capsys.readouterr().err == "mapper stdout\nmapper stderr\n"
