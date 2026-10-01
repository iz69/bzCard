"""Validate every deletion target before removing any owned image directory."""
import re
import shutil

from ..config import settings


IMAGE_FIELDS = ("original_image_path", "processed_image_path", "thumbnail_path")


def owned_image_directories(conn, user_id):
    root = settings.data_dir.resolve()
    cards_root = root / "cards"
    if cards_root.is_symlink():
        raise ValueError("画像保存先がシンボリックリンクのため削除を中止しました")
    cards = conn.execute("SELECT * FROM card_records").fetchall()
    directories = {}
    owners = {row["id"]: row["owner_user_id"] for row in cards}
    for card in cards:
        if card["owner_user_id"] != user_id:
            continue
        card_id = card["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", card_id):
            raise ValueError("名刺の保存先が不正なため削除を中止しました")
        directory = cards_root / card_id
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("名刺の保存先が不正なため削除を中止しました")
        directories[card_id] = directory
    pending = conn.execute("SELECT e.pending_card_id, lc.owner_user_id FROM line_events e JOIN line_connections lc ON lc.id = e.connection_id WHERE e.pending_card_id IS NOT NULL").fetchall()
    for event in pending:
        card_id = event["pending_card_id"]
        if card_id in owners and owners[card_id] != event["owner_user_id"]:
            raise ValueError("LINE画像の所有者が一致しないため削除を中止しました")
        owners[card_id] = event["owner_user_id"]
        if event["owner_user_id"] == user_id:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", card_id):
                raise ValueError("LINE画像の保存先が不正です")
            directory = cards_root / card_id
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise ValueError("LINE画像の保存先が不正です")
            directories[card_id] = directory
    target_paths = set(directories.values())

    def check(card_id, path):
        if not path:
            return
        resolved = (root / path).resolve()
        owner = owners.get(card_id)
        if owner == user_id:
            if directories[card_id] not in resolved.parents:
                raise ValueError("名刺画像が専用フォルダー外を参照しているため削除を中止しました")
        elif target_paths.intersection((resolved, *resolved.parents)):
            raise ValueError("別の利用者が参照する画像があるため削除を中止しました")

    for card in cards:
        for field in (*IMAGE_FIELDS, *(f"back_{field}" for field in IMAGE_FIELDS)):
            check(card["id"], card[field])
    for image in conn.execute("SELECT * FROM card_images"):
        for field in IMAGE_FIELDS:
            check(image["card_id"], image[field])
    return list(directories.values())


def remove_owned_images(directories):
    for directory in directories:
        if directory.exists():
            # Never follow a directory symlink, including one changed after validation.
            if directory.is_symlink():
                raise ValueError("画像保存先が変更されたため削除を中止しました")
            shutil.rmtree(directory)
