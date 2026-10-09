"""雲端服務的 API Key：存在 GNOME 鑰匙圈（Secret Service），不寫進設定檔，也不寫進紀錄。

鑰匙圈以登入密碼加密、登入後自動解鎖。常駐程式不會跳出解鎖視窗（鑰匙圈鎖住時直接回報錯誤），
只有 `danwen key` 指令在需要時才請你解鎖。
"""

from __future__ import annotations

APP = "danwen"


class CredentialError(RuntimeError):
    pass


class KeyringStore:
    def _open(self, unlock: bool):
        import secretstorage

        try:
            connection = secretstorage.dbus_init()
            collection = secretstorage.get_default_collection(connection)
        except secretstorage.exceptions.SecretStorageException as e:
            raise CredentialError(f"無法使用 GNOME 鑰匙圈（{e}）") from e
        if collection.is_locked():
            if unlock:
                collection.unlock()  # 跳出 GNOME 的解鎖視窗
            if collection.is_locked():
                connection.close()
                raise CredentialError("GNOME 鑰匙圈已鎖定")
        return connection, collection

    def _items(self, collection, provider: str | None):
        attributes = {"application": APP}
        if provider:
            attributes["provider"] = provider
        return list(collection.search_items(attributes))

    def get(self, provider: str) -> str | None:
        connection, collection = self._open(unlock=False)
        try:
            items = self._items(collection, provider)
            return items[0].get_secret().decode() if items else None
        finally:
            connection.close()

    def set(self, provider: str, key: str) -> None:
        connection, collection = self._open(unlock=True)
        try:
            collection.create_item(
                f"但聞人語 API Key（{provider}）",
                {"application": APP, "provider": provider},
                key.encode(),
                replace=True,
            )
        finally:
            connection.close()

    def delete(self, provider: str | None = None) -> list[str]:
        """刪除指定服務商的金鑰；provider 為 None 時刪除 danwen 的全部金鑰。回傳刪掉的服務商。"""
        connection, collection = self._open(unlock=True)
        try:
            deleted = []
            for item in self._items(collection, provider):
                deleted.append(item.get_attributes().get("provider", "?"))
                item.delete()
            return deleted
        finally:
            connection.close()

    def providers(self) -> list[str]:
        connection, collection = self._open(unlock=False)
        try:
            return sorted({item.get_attributes().get("provider", "?") for item in self._items(collection, None)})
        finally:
            connection.close()
