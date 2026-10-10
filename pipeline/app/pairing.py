"""Pairing a phone with the laptop by QR code, and the device tokens that follow.

There are no logins yet (future work): whoever can open the laptop's /pair page can pair a phone, so the
portal must only be reachable on the office network. A pair code is short-lived and single use. The token
it buys is long and random, and only its SHA-256 is stored, so a copy of the database cannot act as a phone.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
import socket
import sqlite3
from collections.abc import Callable

from app import db

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O or 1/I to misread off a screen
CODE_LENGTH = 6
CODE_SECONDS = 10 * 60
MAX_DRAWS = 20                     # codes drawn at most, looking for one whose QR code reads back
REUSE_IF_SECONDS_LEFT = 2 * 60     # the page keeps showing a code until it is this close to expiring
SEEN_EVERY = 60                    # last_seen is written at most this often per device


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_code(code: object) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch in CODE_ALPHABET)


def issue_code(conn: sqlite3.Connection, shop_id: str, readable: Callable[[str], bool] | None = None) -> dict:
    """A new pair code. When readable is given (the portal passes qr_reads_back), it judges each code drawn before
    the code is stored, and a code whose QR code would not read back is drawn again."""
    draws = 0
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        draws += 1
        if readable is not None and draws < MAX_DRAWS and not readable(code):
            continue
        created = db.now()
        try:
            with db.transaction(conn):
                conn.execute("INSERT INTO pair_codes (code, shop_id, created, expires) VALUES (?, ?, ?, ?)",
                             (code, shop_id, created, created + CODE_SECONDS))
                db.event(conn, "pair_code_issued", shop_id=shop_id, expires=db.iso(created + CODE_SECONDS))
        except sqlite3.IntegrityError:
            continue      # the same six characters were issued before; draw again
        return db.one(conn, "SELECT * FROM pair_codes WHERE code = ?", code)


def current_code(conn: sqlite3.Connection, shop_id: str, readable: Callable[[str], bool] | None = None) -> dict:
    row = db.one(conn, "SELECT * FROM pair_codes WHERE shop_id = ? AND used IS NULL AND expires > ?"
                       " ORDER BY created DESC LIMIT 1", shop_id, db.now() + REUSE_IF_SECONDS_LEFT)
    return row or issue_code(conn, shop_id, readable)


def pair(conn: sqlite3.Connection, code: object, device_name: str) -> dict | None:
    """A new device for a valid code, or None when the code is wrong, expired or used."""
    code = normalise_code(code)
    with db.transaction(conn):
        row = db.one(conn, "SELECT p.*, s.name AS shop_name FROM pair_codes p JOIN shops s ON s.shop_id = p.shop_id"
                           " WHERE p.code = ? AND p.used IS NULL AND p.expires > ?", code, db.now())
        if row is None:
            db.event(conn, "pair_code_rejected", device_name=device_name)
            return None
        token = secrets.token_urlsafe(36)          # 48 characters
        device_id = db.new_id("dev")
        conn.execute("INSERT INTO devices (device_id, shop_id, name, token_hash, paired_at) VALUES (?, ?, ?, ?, ?)",
                     (device_id, row["shop_id"], device_name, token_hash(token), db.iso()))
        conn.execute("UPDATE pair_codes SET used = ?, device_id = ? WHERE code = ?", (db.now(), device_id, code))
        db.event(conn, "device_paired", shop_id=row["shop_id"], device_id=device_id, device_name=device_name)
    return {"device_id": device_id, "token": token, "shop": row["shop_id"], "shop_name": row["shop_name"]}


def authenticate(conn: sqlite3.Connection, authorization: str | None) -> tuple[dict | None, str | None]:
    """(device, None), or (None, "unauthorised" | "device_revoked")."""
    if not authorization or not authorization.startswith("Bearer "):
        return None, "unauthorised"
    token = authorization[len("Bearer "):].strip()
    if not token:
        return None, "unauthorised"
    device = db.one(conn, "SELECT d.*, s.name AS shop_name FROM devices d JOIN shops s ON s.shop_id = d.shop_id"
                          " WHERE d.token_hash = ?", token_hash(token))
    if device is None:
        return None, "unauthorised"
    if device["revoked_at"]:
        return None, "device_revoked"
    if device["last_seen"] is None or db.now() - device["last_seen"] > SEEN_EVERY:
        conn.execute("UPDATE devices SET last_seen = ? WHERE device_id = ?", (db.now(), device["device_id"]))
    return device, None


def revoke(conn: sqlite3.Connection, device_id: str) -> bool:
    with db.transaction(conn):
        device = db.one(conn, "SELECT * FROM devices WHERE device_id = ? AND revoked_at IS NULL", device_id)
        if device is None:
            return False
        conn.execute("UPDATE devices SET revoked_at = ? WHERE device_id = ?", (db.iso(), device_id))
        db.event(conn, "device_revoked", shop_id=device["shop_id"], device_id=device_id, device_name=device["name"])
    return True


# --- the address phones use, and the QR code carrying it ----------------------------------------

def lan_address() -> str:
    """This laptop's address on the office network, best guess."""
    candidates = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))     # a UDP connect sends nothing; it only picks the outgoing interface
            candidates.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        candidates += [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    usable = [ip for ip in candidates if not ipaddress.ip_address(ip).is_loopback
              and not ipaddress.ip_address(ip).is_link_local]
    private = [ip for ip in usable if ipaddress.ip_address(ip).is_private]
    return (private or usable or ["127.0.0.1"])[0]


def server_url(settings) -> str:
    return (settings.public_url or f"http://{lan_address()}:{settings.port}").rstrip("/")


def qr_payload(settings, code: dict) -> str:
    return json.dumps({"v": 1, "server": server_url(settings), "pair_code": code["code"], "shop": code["shop_id"]})


def _qr_image(text: str, level: int, scale: int):
    import cv2
    params = cv2.QRCodeEncoder_Params()
    params.correction_level = level
    modules = cv2.QRCodeEncoder.create(params).encode(text)
    # OpenCV leaves a two-module white margin; the standard quiet zone is four
    modules = cv2.copyMakeBorder(modules, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=255)
    return cv2.resize(modules, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)


def qr_image(text: str, scale: int = 10):
    """(image, reads_back): the QR code as a grayscale image, each module scaled up to whole pixels (nearest
    neighbour) so it stays crisp.

    OpenCV's own detector cannot read a small share of the codes its encoder makes. In trials on pairing payloads
    about 1 in 100 failed at low error correction, 1 in 300 at medium, and 1 in 200 at both. So the image is decoded
    before use, medium correction first and low if medium does not read back; reads_back says whether either did."""
    import cv2
    detector = cv2.QRCodeDetector()
    first = None
    for level in (cv2.QRCODE_ENCODER_CORRECT_LEVEL_M, cv2.QRCODE_ENCODER_CORRECT_LEVEL_L):
        image = _qr_image(text, level, scale)
        if detector.detectAndDecode(image)[0] == text:
            return image, True
        first = image if first is None else first
    return first, False


def qr_png(text: str, scale: int = 10) -> bytes:
    import cv2
    ok, png = cv2.imencode(".png", qr_image(text, scale)[0])
    if not ok:
        raise RuntimeError("could not encode the QR code image")
    return png.tobytes()


def qr_reads_back(settings, shop_id: str) -> Callable[[str], bool]:
    """For issue_code: does this pair code's QR code, drawn as /pair/qr draws it, decode to its payload?"""
    return lambda code: qr_image(qr_payload(settings, {"code": code, "shop_id": shop_id}))[1]
