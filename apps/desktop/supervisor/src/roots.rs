//! The folders the desktop app may touch (invariant 2). This file is the only way a root comes into being on the
//! desktop: the shell writes it after a native folder dialog, REST and MCP cannot (C-09). The worker reads it through
//! `--roots-file`, and the schema is what `opagent worker roots` writes.

use serde::{Deserialize, Serialize};
use std::io;
use std::path::Path;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Root {
    pub alias: String,
    pub path: String,
    pub mode: String,
}

pub const OUTPUT_ALIAS: &str = "sorted";

/// `[a-z][a-z0-9_-]{0,31}`, the alias rule of `opagent worker roots add`; non-ASCII folder names fall back to `photos`.
fn slug(name: &str) -> String {
    let mut s: String = name
        .to_lowercase()
        .chars()
        .map(|c| match c {
            'a'..='z' | '0'..='9' | '_' | '-' => c,
            _ => '-',
        })
        .collect();
    s = s
        .trim_start_matches(|c: char| !c.is_ascii_lowercase())
        .chars()
        .take(24)
        .collect();
    s = s.trim_end_matches(['-', '_']).to_string();
    if s.is_empty() {
        "photos".to_string()
    } else {
        s
    }
}

fn same_path(a: &str, b: &str) -> bool {
    let norm = |p: &str| p.trim_end_matches(['/', '\\']).to_lowercase();
    if cfg!(windows) {
        norm(a) == norm(b)
    } else {
        a.trim_end_matches('/') == b.trim_end_matches('/')
    }
}

/// The file a photo is, for 「在檔案總管中顯示」: alias + relative path, inside that root after resolving links
/// (invariant 2).
pub fn resolve(roots: &[Root], alias: &str, rel: &str) -> Option<std::path::PathBuf> {
    use std::path::Component;
    let root = roots.iter().find(|r| r.alias == alias)?;
    if rel.is_empty()
        || rel.contains('\0')
        || rel.contains('\\')
        || (cfg!(windows) && rel.contains(':'))
    {
        return None; // the API's paths are '/'-separated and relative; a drive or backslash is not one
    }
    let rp = Path::new(rel);
    if rp.components().any(|c| !matches!(c, Component::Normal(_))) {
        return None; // '..', '.', a root or a prefix
    }
    let base = std::fs::canonicalize(&root.path).ok()?;
    let full = std::fs::canonicalize(base.join(rp)).ok()?; // follows links: where it really is
    full.starts_with(&base).then_some(full)
}

/// Registers `path` and answers its alias. The same folder twice keeps one root: asking for write access upgrades it.
pub fn add(roots: &mut Vec<Root>, path: &Path, mode: &str) -> String {
    let text = path.to_string_lossy().to_string();
    if let Some(r) = roots.iter_mut().find(|r| same_path(&r.path, &text)) {
        if mode == "rw" {
            r.mode = "rw".to_string();
        }
        return r.alias.clone();
    }
    let base = slug(
        &path
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default(),
    );
    let taken = |a: &str| a == OUTPUT_ALIAS || roots.iter().any(|r| r.alias == a);
    let mut alias = base.clone();
    let mut n = 2;
    while taken(&alias) {
        alias = format!("{base}-{n}");
        n += 1;
    }
    roots.push(Root {
        alias: alias.clone(),
        path: text,
        mode: mode.to_string(),
    });
    alias
}

pub fn load(file: &Path) -> Vec<Root> {
    std::fs::read_to_string(file)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

/// Written through a temp file: a crash mid-write must not lose the user's list of folders.
pub fn save(file: &Path, roots: &[Root]) -> io::Result<()> {
    if let Some(dir) = file.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let tmp = file.with_extension("json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(roots)?)?;
    std::fs::rename(&tmp, file)
}

/// First start: an empty, app-owned output folder, so "organize" has somewhere to write and the worker has a root.
pub fn seed_output(file: &Path, pictures: &Path) -> io::Result<Vec<Root>> {
    let mut roots = load(file);
    if file.exists() {
        return Ok(roots);
    }
    let out = pictures.join(crate::paths::NAME);
    std::fs::create_dir_all(&out)?;
    roots.push(Root {
        alias: OUTPUT_ALIAS.to_string(),
        path: out.to_string_lossy().to_string(),
        mode: "rw".to_string(),
    });
    save(file, &roots)?;
    Ok(roots)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("opa-roots-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn desk_062_reveal_resolves_only_inside_a_root() {
        // 回歸 (audit round 27): 「在檔案總管中顯示」 never showed; the shell had no reveal. It resolves a photo as
        // root alias + relative path (invariant 2) and refuses anything that leaves the root.
        let d = tmp("reveal");
        std::fs::create_dir_all(d.join("trip")).unwrap();
        std::fs::write(d.join("trip/a.jpg"), b"x").unwrap();
        std::fs::write(d.parent().unwrap().join("opa-outside.jpg"), b"x").unwrap();
        let r = vec![Root {
            alias: "photos".into(),
            path: d.to_string_lossy().into(),
            mode: "ro".into(),
        }];
        let got = resolve(&r, "photos", "trip/a.jpg").expect("inside");
        assert!(got.ends_with("a.jpg"));
        for bad in [
            "../opa-outside.jpg",
            "trip/../../opa-outside.jpg",
            "/etc/passwd",
            "C:\\x.jpg",
            "",
            "trip/\0a",
        ] {
            assert!(resolve(&r, "photos", bad).is_none(), "{bad}");
        }
        assert!(resolve(&r, "nope", "trip/a.jpg").is_none());
        assert!(resolve(&r, "photos", "trip/missing.jpg").is_none());
    }

    #[test]
    fn desk_044_alias_follows_the_cli_rule() {
        let mut r = vec![];
        assert_eq!(
            add(&mut r, Path::new("/m/My Holiday 2024"), "ro"),
            "my-holiday-2024"
        );
        assert_eq!(add(&mut r, Path::new("/m/2024"), "ro"), "photos"); // must start with a letter
        assert_eq!(add(&mut r, Path::new("/m/家庭照片"), "ro"), "photos-2");
        let long = "x".repeat(60);
        let a = add(&mut r, &PathBuf::from("/m").join(long), "ro");
        assert!(a.len() <= 32, "{a}");
    }

    #[test]
    fn desk_044_same_folder_is_one_root_and_rw_only_ever_upgrades() {
        let mut r = vec![];
        let a = add(&mut r, Path::new("/m/pics"), "ro");
        assert_eq!(add(&mut r, Path::new("/m/pics/"), "ro"), a);
        assert_eq!((r.len(), r[0].mode.as_str()), (1, "ro"));
        add(&mut r, Path::new("/m/pics"), "rw");
        add(&mut r, Path::new("/m/pics"), "ro");
        assert_eq!(r[0].mode, "rw");
    }

    #[test]
    fn desk_044_a_folder_cannot_take_the_output_alias() {
        let mut r = vec![];
        assert_eq!(add(&mut r, Path::new("/m/sorted"), "ro"), "sorted-2");
    }

    #[test]
    fn desk_045_first_start_seeds_one_writable_output_folder_and_never_again() {
        let d = tmp("seed");
        let file = d.join("config").join("roots.json");
        let pictures = d.join("Pictures");
        let roots = seed_output(&file, &pictures).unwrap();
        assert_eq!(roots.len(), 1);
        assert_eq!(
            (roots[0].alias.as_str(), roots[0].mode.as_str()),
            ("sorted", "rw")
        );
        assert!(pictures.join("OpenPhotoAgent").is_dir());
        // the person removed it on purpose: a later start must respect that
        save(&file, &[]).unwrap();
        assert!(seed_output(&file, &pictures).unwrap().is_empty());
    }

    #[test]
    fn desk_045_save_load_round_trip_with_windows_paths() {
        let d = tmp("rt");
        let file = d.join("roots.json");
        let roots = vec![Root {
            alias: "a".into(),
            path: r"D:\照片, 2024:舊".into(),
            mode: "ro".into(),
        }];
        save(&file, &roots).unwrap();
        assert_eq!(load(&file), roots);
    }
}
