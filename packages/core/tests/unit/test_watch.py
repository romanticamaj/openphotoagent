"""Watch folder decisions (05 §5; 09b §6): debounce, size stability, batching, network file systems."""

import pytest
from opagent_core import watch as W


@pytest.mark.unit
@pytest.mark.tc("WATCH-002")
def test_watch_002_only_stable_files_after_the_quiet_period():
    d = W.Debouncer(debounce_s=120, stable_s=10)
    sizes = {"a/big.mov": 100, "a/x.jpg": 5}
    d.event("a/big.mov", now=0)
    d.event("a/x.jpg", now=0)
    assert d.ready(now=60, size_of=sizes.get) == []  # folder still busy
    sizes["a/big.mov"] = 900  # still being written (30 s copy)
    d.event("a/big.mov", now=30)
    assert d.ready(now=149, size_of=sizes.get) == []  # 120 s since the last event not yet passed
    assert d.ready(now=150, size_of=sizes.get) == ["a/x.jpg"]  # big.mov seen at a new size only now
    sizes["a/big.mov"] = 1000  # grew again without an event (some writers do not notify every chunk)
    assert d.ready(now=165, size_of=sizes.get) == []
    assert d.ready(now=175, size_of=sizes.get) == ["a/big.mov"]  # unchanged for 10 s
    assert d.ready(now=999, size_of=sizes.get) == []  # each file once


@pytest.mark.unit
@pytest.mark.tc("WATCH-002")
def test_watch_002_deleted_before_stable_is_dropped_and_folders_are_independent():
    d = W.Debouncer(debounce_s=5, stable_s=0)
    sizes = {"a/1.jpg": 1, "b/2.jpg": 2}
    d.event("a/1.jpg", now=0)
    d.event("b/2.jpg", now=4)
    del sizes["a/1.jpg"]
    assert d.ready(now=5, size_of=sizes.get) == []  # a/1.jpg is gone; b is still in its quiet period
    assert d.ready(now=9, size_of=sizes.get) == ["b/2.jpg"]
    assert d.pending() == 0


@pytest.mark.unit
@pytest.mark.tc("WATCH-005")
def test_watch_005_many_events_one_batch_low_priority():
    d = W.Debouncer(debounce_s=1, stable_s=0)
    for i in range(5000):
        d.event(f"import/{i:05d}.jpg", now=0)
        d.event(f"import/{i:05d}.jpg", now=0.5)  # duplicates collapse
    ready = d.ready(now=2, size_of=lambda p: 1)
    assert len(ready) == 5000
    batches = W.batches(ready)
    assert len(batches) == 1 and batches[0].count == 5000 and batches[0].priority == "low"
    small = W.batches(ready[:20])
    assert small[0].priority == "normal"


@pytest.mark.unit
@pytest.mark.tc("WATCH-006")
@pytest.mark.parametrize(
    ("fstype", "mode"),
    [
        ("ext4", "native"),
        ("apfs", "native"),
        ("ntfs", "native"),
        ("cifs", "poll"),
        ("smb3", "poll"),
        ("smbfs", "poll"),
        ("nfs", "poll"),
        ("nfs4", "poll"),
        ("fuse.sshfs", "poll"),
        ("afpfs", "poll"),
        ("9p", "poll"),
    ],
)
def test_watch_006_network_file_systems_are_polled(fstype, mode):
    assert W.mode_for(fstype) == mode


@pytest.mark.unit
@pytest.mark.tc("WATCH-006")
def test_watch_006_mount_lookup_takes_the_longest_prefix():
    mounts = "/dev/vda / ext4 rw 0 0\n//nas/photos /mnt/nas cifs rw 0 0\nnas:/x /mnt/nas/deep nfs4 rw 0 0\n"
    assert W.fstype_of("/mnt/nas/2024/a.jpg", mounts) == "cifs"
    assert W.fstype_of("/mnt/nas/deep/a.jpg", mounts) == "nfs4"
    assert W.fstype_of("/mnt/nasty", mounts) == "ext4"
    assert W.fstype_of("/home/u/Pictures", mounts) == "ext4"


@pytest.mark.unit
@pytest.mark.tc("WATCH-007")
def test_watch_007_mount_lookup_understands_windows_paths():
    mounts = "Z: Z:/ cifs rw 0 0\n//nas/deep Y:/photos nfs4 rw 0 0\n"
    assert W.fstype_of("Z:\\2024\\a.jpg", mounts) == "cifs"
    assert W.fstype_of("z:\\2024", mounts) == "cifs"  # drive letters are not case sensitive
    assert W.fstype_of("Y:\\photos\\a.jpg", mounts) == "nfs4"
    assert W.fstype_of("Y:\\photos2", mounts) == ""
    assert W.fstype_of("C:\\Users\\u\\Pictures", mounts) == ""


@pytest.mark.unit
@pytest.mark.tc("WATCH-007")
def test_watch_007_a_unc_path_is_a_network_share_without_any_mount_table():
    assert W.mode_for(W.fstype_of("\\\\nas\\photos\\2024", "")) == "poll"
    assert W.mode_for(W.fstype_of("//nas/photos/2024", "")) == "native"  # a POSIX path, however odd


@pytest.mark.unit
@pytest.mark.tc("WATCH-007")
def test_watch_007_mapped_network_drives_become_a_mount_table():
    table = W.windows_mount_table({"C": 3, "D": 3, "E": 2, "Z": 4})  # GetDriveTypeW: fixed, removable, remote
    assert W.mode_for(W.fstype_of("Z:\\photos", table)) == "poll"
    assert W.mode_for(W.fstype_of("C:\\photos", table)) == "native"
    assert W.mode_for(W.fstype_of("E:\\DCIM", table)) == "native"
