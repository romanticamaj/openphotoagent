//! Sharing several photos on the desktop (ADR-0054): the page makes the 2048 px previews (no original is opened,
//! invariant 1) and the shell writes them into a new folder inside one the person picked in the OS dialog. A page
//! cannot name the folder, and a folder inside the library is refused: files appear in a root only through a plan
//! (invariant 3).

use crate::roots::Root;
use std::io;
use std::path::{Path, PathBuf};

/// 「分享 2026-11-14（15 張）」; the day only when it is a plain date.
pub fn folder_name(day: &str, n: usize) -> String {
    let b = day.as_bytes();
    let plain = b.len() == 10
        && b.iter().enumerate().all(|(i, c)| {
            if i == 4 || i == 7 {
                *c == b'-'
            } else {
                c.is_ascii_digit()
            }
        });
    if plain {
        format!("分享 {day}（{n} 張）")
    } else {
        format!("分享（{n} 張）")
    }
}

/// A name the page made with `photoFileName`: `photo-` + letters, digits and `-`, then `.jpg`. Nothing else.
pub fn valid_file_name(name: &str) -> bool {
    let Some(stem) = name
        .strip_prefix("photo-")
        .and_then(|s| s.strip_suffix(".jpg"))
    else {
        return false;
    };
    !stem.is_empty()
        && stem.len() <= 40
        && stem.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')
}

/// The picked folder is a root or lies inside one.
pub fn inside_a_root(roots: &[Root], picked: &Path) -> bool {
    let canon = |p: &Path| std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    let picked = canon(picked);
    roots
        .iter()
        .any(|r| picked.starts_with(canon(Path::new(&r.path))))
}

/// A new folder `name` in `parent`; 「name (2)」, 「name (3)」 … when one is there already.
pub fn new_dir(parent: &Path, name: &str) -> io::Result<PathBuf> {
    for k in 1..1000 {
        let p = if k == 1 {
            parent.join(name)
        } else {
            parent.join(format!("{name} ({k})"))
        };
        match std::fs::create_dir(&p) {
            Ok(()) => return Ok(p),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        "no free folder name",
    ))
}

/// One photo into the share folder: a valid name, never over a file that is there.
pub fn write_new(dir: &Path, name: &str, bytes: &[u8]) -> io::Result<()> {
    use std::io::Write;
    if !valid_file_name(name) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "not a photo name",
        ));
    }
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(dir.join(name))?;
    f.write_all(bytes)?;
    f.sync_all()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(tag: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("opa-share-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn share_012_the_folder_says_the_day_and_how_many() {
        // 回歸 (audit round 124): photos could be shared one at a time only. Several go into one new folder.
        assert_eq!(folder_name("2026-11-14", 15), "分享 2026-11-14（15 張）");
        assert_eq!(folder_name("../../x", 2), "分享（2 張）");
    }

    #[test]
    fn share_013_only_names_the_page_makes() {
        assert!(valid_file_name("photo-2025-11-11-0c1e2a11.jpg"));
        assert!(valid_file_name("photo-0c1e2a11.jpg"));
        for bad in [
            "../photo-a.jpg",
            "photo-a/b.jpg",
            "photo-a\\b.jpg",
            "a.jpg",
            "photo-a.exe",
            "photo-.jpg",
            "photo-a.jpg.",
            "CON",
            "",
        ] {
            assert!(!valid_file_name(bad), "{bad}");
        }
    }

    #[test]
    fn share_014_a_folder_in_the_library_is_refused() {
        let d = tmp("roots");
        std::fs::create_dir_all(d.join("lib/trip")).unwrap();
        std::fs::create_dir_all(d.join("out")).unwrap();
        let roots = vec![Root {
            alias: "lib".into(),
            path: d.join("lib").to_string_lossy().into(),
            mode: "ro".into(),
        }];
        assert!(inside_a_root(&roots, &d.join("lib")));
        assert!(inside_a_root(&roots, &d.join("lib/trip")));
        assert!(!inside_a_root(&roots, &d.join("out")));
        assert!(!inside_a_root(&roots, &d)); // the new folder is made beside the library, not in it
    }

    #[test]
    fn share_015_a_second_share_gets_its_own_folder_and_nothing_is_overwritten() {
        let d = tmp("dirs");
        let a = new_dir(&d, "分享（2 張）").unwrap();
        let b = new_dir(&d, "分享（2 張）").unwrap();
        assert_ne!(a, b);
        assert_eq!(b.file_name().unwrap().to_string_lossy(), "分享（2 張） (2)");
        write_new(&a, "photo-0c1e2a11.jpg", b"one").unwrap();
        assert!(write_new(&a, "photo-0c1e2a11.jpg", b"two").is_err());
        assert_eq!(std::fs::read(a.join("photo-0c1e2a11.jpg")).unwrap(), b"one");
        assert!(write_new(&a, "../photo-x.jpg", b"x").is_err());
        assert!(!d.join("photo-x.jpg").exists());
    }
}
