"""Tests for the door knowledge store."""

from __future__ import annotations

import json

from src.memory import DoorMemory


class TestScoring:
    def test_an_untried_door_sits_in_the_middle(self):
        assert DoorMemory().score(1, 1) == 0.5

    def test_success_raises_the_score_above_untried(self):
        memory = DoorMemory()
        memory.record(1, 1, success=True)
        assert memory.score(1, 1) > 0.5

    def test_failure_drops_the_score_below_untried(self):
        memory = DoorMemory()
        memory.record(1, 1, success=False)
        assert memory.score(1, 1) < 0.5

    def test_a_single_setback_does_not_erase_a_good_record(self):
        # Live probing showed reliable doors occasionally dead-end, so one
        # failure must not demote a door below an untried one.
        memory = DoorMemory()
        for _ in range(5):
            memory.record(2, 2, success=True)
        memory.record(2, 2, success=False)
        assert memory.score(2, 2) > 0.5


class TestChoose:
    def test_prefers_the_proven_door(self):
        memory = DoorMemory()
        memory.record(1, 2, success=True)
        memory.record(1, 2, success=True)
        assert memory.choose(1, [1, 2, 3]) == 2

    def test_prefers_an_untried_door_over_a_failed_one(self):
        memory = DoorMemory()
        memory.record(1, 1, success=False)
        assert memory.choose(1, [1, 2]) == 2

    def test_avoids_the_worst_door(self):
        memory = DoorMemory()
        memory.record(1, 1, success=False)
        memory.record(1, 1, success=False)
        memory.record(1, 2, success=True)
        assert memory.choose(1, [1, 2, 3]) != 1

    def test_explores_when_nothing_is_known(self):
        memory = DoorMemory()
        seen = {memory.choose(1, [1, 2, 3]) for _ in range(60)}
        # Ties are broken at random, so all doors should come up.
        assert seen == {1, 2, 3}

    def test_only_returns_an_offered_door(self):
        memory = DoorMemory()
        memory.record(5, 9, success=True)
        assert memory.choose(5, [1, 2]) in (1, 2)


class TestPersistence:
    def test_survives_a_reload(self, tmp_path):
        path = tmp_path / "memory.json"
        memory = DoorMemory(path)
        memory.record(3, 2, success=True)
        memory.record(3, 1, success=False)
        memory.save()

        reloaded = DoorMemory(path)
        assert reloaded.tally(3, 2) == (1, 0)
        assert reloaded.tally(3, 1) == (0, 1)

    def test_creates_the_directory(self, tmp_path):
        path = tmp_path / "nested" / "memory.json"
        memory = DoorMemory(path)
        memory.record(1, 1, success=True)
        memory.save()
        assert path.is_file()

    def test_starts_clean_when_the_file_is_missing(self, tmp_path):
        assert DoorMemory(tmp_path / "absent.json").tally(1, 1) == (0, 0)

    def test_ignores_a_corrupt_file(self, tmp_path):
        path = tmp_path / "memory.json"
        path.write_text("{not json", encoding="utf-8")
        # A damaged file must not stop the bot from running.
        assert DoorMemory(path).tally(1, 1) == (0, 0)

    def test_writes_readable_json(self, tmp_path):
        path = tmp_path / "memory.json"
        memory = DoorMemory(path)
        memory.record(2, 3, success=True)
        memory.save()
        assert json.loads(path.read_text(encoding="utf-8"))["rooms"]["2"]["3"] == [1, 0]

    def test_without_a_path_nothing_is_written(self, tmp_path):
        memory = DoorMemory(None)
        memory.record(1, 1, success=True)
        memory.save()
        assert list(tmp_path.iterdir()) == []
