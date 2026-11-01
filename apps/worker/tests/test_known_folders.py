import pytest

from opagent_worker.known_folders import default_output_dir


@pytest.mark.integration
@pytest.mark.tc("ORG-003")
def test_org_003_windows_uses_known_folder_including_onedrive_redirect():
    redirected = r"C:\Users\amy\OneDrive\圖片"
    got = default_output_dir("windows", "zh-TW", known_folder=lambda fid: redirected, home=r"C:\Users\amy")
    assert got == redirected + "\\OpenPhotoAgent 整理結果"
    assert default_output_dir("windows", "en", known_folder=lambda fid: r"D:\Pics", home="x") == r"D:\Pics\OpenPhotoAgent Sorted"


@pytest.mark.integration
@pytest.mark.tc("ORG-003")
def test_org_003_linux_xdg_and_fallback():
    calls = []

    def run(cmd):
        calls.append(cmd)
        return "/home/amy/相片\n"

    assert default_output_dir("linux", "zh-TW", run=run, home="/home/amy") == "/home/amy/相片/OpenPhotoAgent 整理結果"
    assert calls == [["xdg-user-dir", "PICTURES"]]

    def missing(cmd):
        raise FileNotFoundError

    assert default_output_dir("linux", "en", run=missing, home="/home/amy") == "/home/amy/Pictures/OpenPhotoAgent Sorted"
    # xdg-user-dir prints $HOME when PICTURES is unset: do not dump results into the home directory itself
    assert default_output_dir("linux", "en", run=lambda c: "/home/amy\n", home="/home/amy") == "/home/amy/Pictures/OpenPhotoAgent Sorted"


@pytest.mark.integration
@pytest.mark.tc("ORG-003")
def test_org_003_macos_pictures():
    assert default_output_dir("macos", "zh-TW", home="/Users/amy") == "/Users/amy/Pictures/OpenPhotoAgent 整理結果"
