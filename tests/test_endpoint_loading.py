# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

import subprocess

import pytest

import kgi.endpoints as endpoints


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _FakeProcess:
    pid = 12345
    returncode = None

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


class _FakeSparqlClient:
    updates: list[str] = []

    def __init__(self, url: str):
        self.url = url

    def update(self, query: str):
        self.updates.append(query)
        raise AssertionError(f"SPARQL update was called: {query}")

    def query(self, query: str, method: str = "GET"):
        return {
            "head": {"vars": ["count"]},
            "results": {
                "bindings": [
                    {
                        "count": {
                            "type": "literal",
                            "value": "1",
                        }
                    }
                ]
            },
        }

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_fake_client() -> None:
    _FakeSparqlClient.updates = []


def _patch_processes(monkeypatch) -> None:
    monkeypatch.setattr(endpoints, "SPARQLClient", _FakeSparqlClient)
    monkeypatch.setattr(endpoints, "urlopen", lambda url, timeout=1: _FakeResponse())
    monkeypatch.setattr(
        endpoints.subprocess, "Popen", lambda *args, **kwargs: _FakeProcess()
    )
    monkeypatch.setattr(endpoints.os, "getpgid", lambda pid: 12345)
    monkeypatch.setattr(
        endpoints.os, "killpg", lambda process_group_id, signal_number: None
    )


def test_qlever_endpoint_loads_by_index_without_sparql_update(
    monkeypatch, tmp_path
) -> None:
    rdf_file = tmp_path / "data.nt"
    rdf_file.write_text('<http://example.com/s> <http://example.com/p> "o" .\n')

    _patch_processes(monkeypatch)
    monkeypatch.setattr(
        endpoints.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )

    endpoint = endpoints.QLeverEndpoint("http://localhost:7019", str(rdf_file))
    try:
        endpoint.query("SELECT * WHERE { ?s ?p ?o }")
    finally:
        endpoint.close()

    assert _FakeSparqlClient.updates == []
