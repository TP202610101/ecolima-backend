"""
tests/test_dataset_storage.py
Pruebas de la capa de almacenamiento de datasets (Blob vs. disco local).
No golpea Azure real: mockea get_container_client con un contenedor falso
en memoria, y prueba el fallback local con Settings() real (placeholder
en el entorno de test).
"""
from pathlib import Path

import pytest

from app.services import dataset_service


class _FakeDownload:
    def __init__(self, data: bytes):
        self._data = data

    def readall(self) -> bytes:
        return self._data


class _FakeBlobClient:
    def __init__(self, store: dict, blob_name: str):
        self._store = store
        self.blob_name = blob_name

    def exists(self) -> bool:
        return self.blob_name in self._store

    def upload_blob(self, data, overwrite=True):
        self._store[self.blob_name] = data

    def download_blob(self) -> _FakeDownload:
        return _FakeDownload(self._store[self.blob_name])

    def delete_blob(self) -> None:
        self._store.pop(self.blob_name, None)


class _FakeContainer:
    def __init__(self):
        self.store: dict[str, bytes] = {}

    def get_blob_client(self, name: str) -> _FakeBlobClient:
        return _FakeBlobClient(self.store, name)

    def upload_blob(self, name: str, data, overwrite=True) -> None:
        self.store[name] = data


@pytest.fixture
def fake_blob_container(monkeypatch):
    """Simula Blob Storage 'configurado' con un contenedor en memoria."""
    container = _FakeContainer()
    monkeypatch.setattr(dataset_service, "_datasets_container", lambda: container)
    return container


def test_upload_with_blob_configured_writes_to_blob_not_disk(fake_blob_container, tmp_path, monkeypatch):
    monkeypatch.setattr(dataset_service, "UPLOAD_DIR", tmp_path)

    key = dataset_service.save_uploaded_dataset_file(990001, "puntos.csv", b"latitude,longitude\n1,2\n")

    assert key == "990001.csv"
    assert "990001.csv" in fake_blob_container.store
    assert not (tmp_path / "990001.csv").exists()


def test_load_dataset_reads_back_from_blob(fake_blob_container):
    raw = b"latitude,longitude\n-12.1,-77.0\n"
    dataset_service.save_uploaded_dataset_file(990002, "puntos.csv", raw)

    df, size = dataset_service._load_dataset_file(990002)

    assert list(df.columns) == ["latitude", "longitude"]
    assert len(df) == 1
    assert size == len(raw)


def test_write_dataset_file_updates_existing_blob(fake_blob_container):
    import pandas as pd

    dataset_service.save_uploaded_dataset_file(990003, "puntos.csv", b"latitude,longitude\n1,2\n")
    df = pd.DataFrame({"latitude": [9.9], "longitude": [8.8]})

    dataset_service._write_dataset_file(990003, df)

    updated, _ = dataset_service._load_dataset_file(990003)
    assert updated["latitude"].iloc[0] == 9.9


def test_delete_dataset_file_removes_blob(fake_blob_container):
    dataset_service.save_uploaded_dataset_file(990004, "puntos.csv", b"a,b\n1,2\n")
    assert "990004.csv" in fake_blob_container.store

    dataset_service.delete_dataset_file(990004)

    assert "990004.csv" not in fake_blob_container.store


def test_load_missing_dataset_from_blob_returns_404(fake_blob_container):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        dataset_service._load_dataset_file(990005)
    assert exc_info.value.status_code == 404


def test_upload_without_blob_configured_falls_back_to_local_disk(tmp_path, monkeypatch):
    """Sin Blob configurado (placeholder en Settings), sigue usando disco local --
    comportamiento actual, necesario para que los tests corran sin cuenta de Azure."""
    monkeypatch.setattr(dataset_service, "UPLOAD_DIR", tmp_path)

    path_str = dataset_service.save_uploaded_dataset_file(990006, "puntos.csv", b"a,b\n1,2\n")

    assert Path(path_str) == tmp_path / "990006.csv"
    assert (tmp_path / "990006.csv").exists()

    dataset_service.delete_dataset_file(990006)
    assert not (tmp_path / "990006.csv").exists()
