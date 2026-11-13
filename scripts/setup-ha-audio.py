#!/usr/bin/env python3
"""
Setup Home Assistant configuration entries for VLC Telnet and Wyoming Piper,
and generate a Long-Lived Access Token for Joshua Johnson.
"""

import json
import os
import secrets
import shutil
import time
import uuid
from datetime import datetime, timezone, timedelta
import jwt

BASE_DIR = "/home/jake/HomeServer/data/homeassistant/.storage"
CONFIG_ENTRIES_FILE = os.path.join(BASE_DIR, "core.config_entries")
AUTH_FILE = os.path.join(BASE_DIR, "auth")
TOKEN_FILE = "/home/jake/HomeServer/.ha_token"

def generate_ulid():
    # 26-char Crockford's Base32
    crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    timestamp_ms = int(time.time() * 1000)
    res = []
    # 10 chars for 48-bit timestamp
    for _ in range(10):
        res.append(crockford[timestamp_ms % 32])
        timestamp_ms //= 32
    res = res[::-1]
    # 16 random chars
    rand_bytes = secrets.token_bytes(10)
    rand_int = int.from_bytes(rand_bytes, "big")
    for _ in range(16):
        res.append(crockford[rand_int % 32])
        rand_int //= 32
    return "".join(res)

def update_config_entries():
    print(f"Reading {CONFIG_ENTRIES_FILE}...")
    shutil.copy2(CONFIG_ENTRIES_FILE, CONFIG_ENTRIES_FILE + ".bak_speaker")
    with open(CONFIG_ENTRIES_FILE, "r") as f:
        data = json.load(f)

    entries = data["data"]["entries"]
    existing_domains = {e["domain"] for e in entries}
    print(f"Existing domains: {existing_domains}")

    now_iso = datetime.now(timezone.utc).isoformat()

    if "vlc_telnet" not in existing_domains:
        vlc_entry = {
            "created_at": now_iso,
            "data": {
                "host": "127.0.0.1",
                "password": "jbl_speaker_secret_2026",
                "port": 4212,
            },
            "disabled_by": None,
            "discovery_keys": {},
            "domain": "vlc_telnet",
            "entry_id": generate_ulid(),
            "minor_version": 1,
            "modified_at": now_iso,
            "options": {},
            "pref_disable_new_entities": False,
            "pref_disable_polling": False,
            "source": "user",
            "subentries": [],
            "title": "JBL Clip 3",
            "unique_id": None,
            "version": 1,
        }
        entries.append(vlc_entry)
        print("Added vlc_telnet config entry.")
    else:
        print("vlc_telnet already exists.")

    if "wyoming" not in existing_domains:
        wyoming_entry = {
            "created_at": now_iso,
            "data": {
                "host": "127.0.0.1",
                "port": 10200,
            },
            "disabled_by": None,
            "discovery_keys": {},
            "domain": "wyoming",
            "entry_id": generate_ulid(),
            "minor_version": 1,
            "modified_at": now_iso,
            "options": {},
            "pref_disable_new_entities": False,
            "pref_disable_polling": False,
            "source": "user",
            "subentries": [],
            "title": "Piper",
            "unique_id": None,
            "version": 1,
        }
        entries.append(wyoming_entry)
        print("Added wyoming config entry.")
    else:
        print("wyoming already exists.")

    with open(CONFIG_ENTRIES_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print("Saved core.config_entries.")

def update_auth_and_generate_token():
    print(f"Reading {AUTH_FILE}...")
    shutil.copy2(AUTH_FILE, AUTH_FILE + ".bak_speaker")
    with open(AUTH_FILE, "r") as f:
        auth_data = json.load(f)

    # Find Joshua Johnson user
    joshua_user = None
    for u in auth_data["data"]["users"]:
        if u.get("name") == "Joshua Johnson":
            joshua_user = u
            break
    
    if not joshua_user:
        raise RuntimeError("User Joshua Johnson not found in auth!")

    user_id = joshua_user["id"]
    print(f"Found user Joshua Johnson: {user_id}")

    # Check for existing LLAT
    existing_llat = None
    for rt in auth_data["data"]["refresh_tokens"]:
        if rt["user_id"] == user_id and rt.get("client_name") == "Antigravity Smart Speaker":
            existing_llat = rt
            break

    now = datetime.now(timezone.utc)
    expiration_seconds = 3650 * 86400  # 10 years

    if existing_llat:
        print("Found existing LLAT refresh token, regenerating access token...")
        jwt_key = existing_llat["jwt_key"]
        token_id = existing_llat["id"]
    else:
        print("Creating new LLAT refresh token...")
        token_id = uuid.uuid4().hex
        jwt_key = secrets.token_hex(64)
        new_rt = {
            "id": token_id,
            "user_id": user_id,
            "client_id": None,
            "client_name": "Antigravity Smart Speaker",
            "client_icon": None,
            "token_type": "long_lived_access_token",
            "created_at": now.isoformat(),
            "access_token_expiration": float(expiration_seconds),
            "token": secrets.token_hex(64),
            "jwt_key": jwt_key,
            "last_used_at": None,
            "last_used_ip": None,
            "expire_at": None,
            "credential_id": None,
            "version": "2026.11.1",
        }
        auth_data["data"]["refresh_tokens"].append(new_rt)

        with open(AUTH_FILE, "w") as f:
            json.dump(auth_data, f, indent=2)
        print("Saved auth data.")

    # Encode JWT access token
    payload = {
        "iss": token_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expiration_seconds)).timestamp()),
    }
    encoded_token = jwt.encode(payload, jwt_key, algorithm="HS256")

    with open(TOKEN_FILE, "w") as f:
        f.write(encoded_token.strip() + "\n")
    os.chmod(TOKEN_FILE, 0o600)
    print(f"Wrote Long-Lived Access Token to {TOKEN_FILE}.")

if __name__ == "__main__":
    update_config_entries()
    update_auth_and_generate_token()

