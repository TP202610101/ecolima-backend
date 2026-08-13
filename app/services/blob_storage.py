# Helper de conexión a Azure Blob Storage, compartido entre modelos
# (ml_service.py) y datasets (dataset_service.py) -- mismo patrón de
# connection string y mismo chequeo de placeholder para ambos.

PLACEHOLDER_MARKER = "AccountName=..."


def is_configured(connection_string: str | None) -> bool:
    """True si hay una connection string real (no vacía, no el placeholder de ejemplo)."""
    return bool(connection_string) and PLACEHOLDER_MARKER not in connection_string


def get_container_client(connection_string: str, container_name: str):
    from azure.storage.blob import BlobServiceClient
    client = BlobServiceClient.from_connection_string(connection_string)
    return client.get_container_client(container_name)
