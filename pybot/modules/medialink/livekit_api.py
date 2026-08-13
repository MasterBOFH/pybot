"""LiveKit room/token client for the medialink module."""

from __future__ import annotations

import datetime
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime as DateTime
from datetime import timedelta
from typing import Any

log = logging.getLogger("pybot.modules.medialink")

AnnounceFn = Callable[[str, str], Awaitable[None] | None]


class LiveKitAPI:
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        server_url: str,
        token_ttl_minutes: int = 60,
        announcements: dict[str, Any] | None = None,
        command_prefix: str = "$",
        announce: AnnounceFn | None = None,
        announce_room: Callable[[str, str], Awaitable[None] | None] | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.server_url = server_url
        self.token_ttl_minutes = token_ttl_minutes
        self.command_prefix = command_prefix
        self._announce = announce
        self._announce_room = announce_room

        ann = announcements or {}
        self.announcements_enabled = bool(ann.get("enabled", True))
        self.announcement_interval_minutes = int(ann.get("interval_minutes", 15))
        self.last_announcement_time: DateTime | None = None

        self.room_service: Any = None
        self.room_cache: dict[str, dict[str, Any]] = {}
        self.participant_cache: dict[str, dict[str, dict[str, Any]]] = {}
        self._lifecycle_emitted_at: dict[tuple[str, str, str], float] = {}

    def should_announce_lifecycle(
        self,
        event: str,
        room_name: str,
        sid: str | None = None,
        *,
        window_seconds: float = 3.0,
    ) -> bool:
        """Return true when a lifecycle event should be announced.

        This de-duplicates near-simultaneous poll/webhook lifecycle events.
        """
        now = time.time()
        key = (event, room_name.casefold(), sid or "")
        last = self._lifecycle_emitted_at.get(key)
        if last is not None and (now - last) < window_seconds:
            return False
        self._lifecycle_emitted_at[key] = now

        # Keep the in-memory dedupe map bounded.
        if len(self._lifecycle_emitted_at) > 512:
            cutoff = now - 300
            self._lifecycle_emitted_at = {
                k: ts for k, ts in self._lifecycle_emitted_at.items() if ts >= cutoff
            }
        return True

    async def connect(self) -> None:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("LiveKit API credentials not configured")
        import livekit.api as api

        self.room_service = api.LiveKitAPI(
            url=self.server_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        )
        try:
            await self.room_service.room.list_rooms(api.ListRoomsRequest())
            log.info("LiveKit connected (%s)", self.server_url)
        except Exception as exc:
            self.room_service = None
            log.warning("LiveKit connection test failed; service unavailable: %s", exc)

    async def close(self) -> None:
        if self.room_service is not None:
            try:
                await self.room_service.aclose()
            except Exception:
                log.exception("Error closing LiveKit room service")
            self.room_service = None

    async def _ensure(self) -> Any:
        if self.room_service is None:
            await self.connect()
        if self.room_service is None:
            raise RuntimeError("LiveKit not configured")
        return self.room_service

    async def create_room(self, room_name: str, **kwargs: Any) -> Any:
        import livekit.api as api

        svc = await self._ensure()
        request = api.CreateRoomRequest(name=room_name, **kwargs)
        room = await svc.room.create_room(request)
        log.info("Created LiveKit room %s", room_name)
        return room

    async def delete_room(self, room_name: str) -> None:
        import livekit.api as api

        svc = await self._ensure()
        await svc.room.delete_room(api.DeleteRoomRequest(room=room_name))
        self.room_cache.pop(room_name, None)
        self.participant_cache.pop(room_name, None)
        log.info("Deleted LiveKit room %s", room_name)

    async def remove_participant(self, room_name: str, identity: str) -> None:
        import livekit.api as api

        svc = await self._ensure()
        await svc.room.remove_participant(
            api.RoomParticipantIdentity(room=room_name, identity=identity)
        )
        log.info("Removed %s from LiveKit room %s", identity, room_name)

    async def update_participant_metadata(
        self, room_name: str, identity: str, metadata: str
    ) -> None:
        import livekit.api as api

        svc = await self._ensure()
        await svc.room.update_participant(
            api.UpdateParticipantRequest(
                room=room_name, identity=identity, metadata=metadata
            )
        )

    async def update_participant_permissions(
        self, room_name: str, identity: str, *, is_admin: bool
    ) -> None:
        import livekit.api as api

        svc = await self._ensure()
        await svc.room.update_participant(
            api.UpdateParticipantRequest(
                room=room_name,
                identity=identity,
                permission=api.ParticipantPermission(
                    can_subscribe=True,
                    can_publish=True,
                    can_publish_data=True,
                    recorder=is_admin,
                    hidden=False,
                    agent=is_admin,
                ),
            )
        )
        log.info(
            "Updated permissions for %s in %s (admin=%s)", identity, room_name, is_admin
        )

    def generate_access_token(
        self, room_name: str, participant_identity: str, **kwargs: Any
    ) -> str:
        from livekit.api import AccessToken, VideoGrants

        if not self.api_key or not self.api_secret:
            raise RuntimeError("LiveKit API credentials not configured")

        ttl = datetime.timedelta(minutes=self.token_ttl_minutes)
        grants = VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=kwargs.get("can_publish", True),
            can_subscribe=kwargs.get("can_subscribe", True),
            can_publish_data=kwargs.get("can_publish_data", True),
            recorder=kwargs.get("recorder", False),
            agent=kwargs.get("agent", False),
        )
        token = (
            AccessToken(self.api_key, self.api_secret)
            .with_identity(participant_identity)
            .with_name(participant_identity)
            .with_ttl(ttl)
            .with_grants(grants)
            .to_jwt()
        )
        log.info(
            "Generated token for %s in %s (TTL %smin)",
            participant_identity,
            room_name,
            self.token_ttl_minutes,
        )
        return token

    def get_active_rooms(self) -> list[dict[str, Any]]:
        now = DateTime.now()
        out: list[dict[str, Any]] = []
        for room_name, room_data in self.room_cache.items():
            last = room_data.get("last_seen")
            if last and (now - last).total_seconds() > 300:
                continue
            participants = []
            for pid, pdata in (self.participant_cache.get(room_name) or {}).items():
                participants.append(
                    {
                        "identity": pdata.get("identity", pid),
                        "name": pdata.get("name", pid),
                        "state": pdata.get("state", "unknown"),
                    }
                )
            out.append(
                {
                    "name": room_data.get("name", room_name),
                    "sid": room_data.get("sid", ""),
                    "creation_time": room_data.get("creation_time", "Unknown"),
                    "num_participants": room_data.get("num_participants", 0),
                    "participants": participants,
                }
            )
        return out

    async def poll_rooms(self) -> None:
        """Refresh room cache; announce create/join/leave/end + periodic reminders."""
        import livekit.api as api

        try:
            svc = await self._ensure()
        except Exception as exc:
            log.warning("LiveKit poll skipped: %s", exc)
            return

        try:
            rooms_response = await svc.room.list_rooms(api.ListRoomsRequest())
        except Exception as exc:
            self.room_service = None
            await self.close()
            log.warning("LiveKit room poll failed; backend unavailable or dropped: %s", exc)
            return

        try:
            rooms = list(rooms_response.rooms or [])
            current_names: set[str] = set()

            for room in rooms:
                current_names.add(room.name)
                if room.name not in self.room_cache:
                    self.room_cache[room.name] = {
                        "name": room.name,
                        "sid": room.sid,
                        "creation_time": room.creation_time,
                        "num_participants": room.num_participants,
                        "last_seen": DateTime.now(),
                    }
                    if self.should_announce_lifecycle(
                        "room_started", room.name, room.sid
                    ):
                        await self._emit(
                            "info",
                            f"🏠 New LiveKit room created: \x02{room.name}\x02 | "
                            f"👥 {room.num_participants} participants",
                        )
                else:
                    old = self.room_cache[room.name]["num_participants"]
                    self.room_cache[room.name].update(
                        {
                            "num_participants": room.num_participants,
                            "last_seen": DateTime.now(),
                        }
                    )
                    if room.num_participants != old:
                        verb = "joined" if room.num_participants > old else "left"
                        await self._emit(
                            "info",
                            f"👋 Participant {verb} room \x02{room.name}\x02 | "
                            f"👥 Now {room.num_participants} participants",
                        )

            stale = []
            for name, data in self.room_cache.items():
                if name in current_names:
                    continue
                if DateTime.now() - data["last_seen"] > timedelta(minutes=2):
                    stale.append(name)
                    sid = str(data.get("sid") or "")
                    if self.should_announce_lifecycle("room_finished", name, sid):
                        await self._emit("info", f"🏠 Room \x02{name}\x02 has ended")
            for name in stale:
                del self.room_cache[name]
                self.participant_cache.pop(name, None)

            await self._periodic_announcements(rooms)
        except Exception:
            log.exception("Error updating LiveKit room status")

    async def _periodic_announcements(self, rooms: list[Any]) -> None:
        if not self.announcements_enabled or not self._announce_room:
            return
        now = DateTime.now()
        if self.last_announcement_time is not None:
            if now - self.last_announcement_time < timedelta(
                minutes=self.announcement_interval_minutes
            ):
                return
        sent = 0
        for room in rooms:
            if room.num_participants <= 0:
                continue
            if room.num_participants == 1:
                msg = (
                    f"🎥 Video chat ongoing — \x021 participant\x02. "
                    f"Type \x02{self.command_prefix}join\x02 to join."
                )
            else:
                msg = (
                    f"🎥 Video chat ongoing — \x02{room.num_participants} participants\x02. "
                    f"Type \x02{self.command_prefix}join\x02 to join."
                )
            result = self._announce_room(room.name, msg)
            if hasattr(result, "__await__"):
                await result
            sent += 1
        if sent:
            self.last_announcement_time = now

    async def _emit(self, event_type: str, text: str) -> None:
        if not self._announce:
            return
        result = self._announce(event_type, text)
        if hasattr(result, "__await__"):
            await result

    def note_participant(
        self, room_name: str, identity: str, name: str | None, *, joined: bool
    ) -> None:
        room = self.participant_cache.setdefault(room_name, {})
        if joined:
            room[identity] = {
                "identity": identity,
                "name": name or identity,
                "state": "active",
                "seen_at": time.time(),
            }
        else:
            room.pop(identity, None)

    def note_room_started(self, room_name: str, room_data: dict[str, Any] | None = None) -> None:
        """Update room cache from webhook lifecycle events.

        This keeps poll-based announcements in sync with webhook ordering.
        """
        data = room_data or {}
        existing = self.room_cache.get(room_name, {})
        self.room_cache[room_name] = {
            "name": data.get("name") or existing.get("name") or room_name,
            "sid": data.get("sid") or existing.get("sid", ""),
            "creation_time": (
                data.get("creation_time")
                or existing.get("creation_time")
                or "Unknown"
            ),
            "num_participants": int(
                data.get("num_participants")
                or existing.get("num_participants")
                or 0
            ),
            "last_seen": DateTime.now(),
        }

    def note_room_finished(self, room_name: str) -> None:
        """Remove ended room from caches when webhook reports completion."""
        self.room_cache.pop(room_name, None)
        self.participant_cache.pop(room_name, None)
