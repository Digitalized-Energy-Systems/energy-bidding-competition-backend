import os
import secrets
import time
from typing import Dict, List, Optional
from pydantic import BaseModel


class IssuedKey(BaseModel):
    name: str
    participant_id: str
    ip: str
    issued_at: float


class _IssuedKeys(BaseModel):
    keys: Dict[str, IssuedKey] = {}


class RegistrationKeys:
    """Registration keys handed out by the backend, persisted to a JSON file
    so that they survive a restart."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.keys: Dict[str, IssuedKey] = {}
        if os.path.exists(file_path):
            with open(file_path) as f:
                self.keys = _IssuedKeys.model_validate_json(f.read()).keys

    def get(self, key: str) -> Optional[IssuedKey]:
        return self.keys.get(key)

    def count_for_ip(self, ip: str) -> int:
        return sum(1 for issued in self.keys.values() if issued.ip == ip)

    def names(self) -> List[str]:
        return [issued.name for issued in self.keys.values()]

    def issue(self, name: str, ip: str) -> str:
        key = secrets.token_urlsafe(16)
        # the ui and score.py show a participant id without its last two
        # characters, so the public id is the name plus two characters
        participant_id = name + secrets.token_hex(1)
        self.keys[key] = IssuedKey(
            name=name, participant_id=participant_id, ip=ip, issued_at=time.time()
        )
        self._save()
        return key

    def _save(self):
        tmp_path = f"{self.file_path}.tmp"
        with open(tmp_path, "w") as f:
            f.write(_IssuedKeys(keys=self.keys).model_dump_json(indent=2))
        os.replace(tmp_path, self.file_path)
