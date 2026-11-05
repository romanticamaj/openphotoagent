//! Where the app keeps its data (08 §1.5). `apps/cli/src/opagent_cli/paths.py` is the same table: both sides have to
//! agree, or `opagent mcp` looks for `run/api.json` in a folder the shell never wrote.

use std::path::{Path, PathBuf};

pub const NAME: &str = "OpenPhotoAgent";
pub const LOWER: &str = "openphotoagent";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Os {
    Windows,
    MacOs,
    Linux,
}

impl Os {
    pub fn current() -> Os {
        if cfg!(windows) {
            Os::Windows
        } else if cfg!(target_os = "macos") {
            Os::MacOs
        } else {
            Os::Linux
        }
    }
}

/// `env` is injected so every OS's answer is testable on any OS. `home` is the user's home directory.
pub fn data_home(os: Os, env: &dyn Fn(&str) -> Option<String>, home: &str) -> PathBuf {
    let set = |k: &str| env(k).filter(|v| !v.is_empty());
    if let Some(h) = set("OPAGENT_HOME") {
        return PathBuf::from(h); // portable mode overrides every location (DESK-025)
    }
    match os {
        Os::Windows => {
            PathBuf::from(set("LOCALAPPDATA").unwrap_or_else(|| format!("{home}\\AppData\\Local")))
                .join(NAME)
        }
        Os::MacOs => PathBuf::from(home)
            .join("Library")
            .join("Application Support")
            .join(NAME),
        Os::Linux => {
            PathBuf::from(set("XDG_DATA_HOME").unwrap_or_else(|| format!("{home}/.local/share")))
                .join(LOWER)
        }
    }
}

/// Tauri canonicalizes resource paths into verbatim form on Windows. Programs that look for siblings next to their
/// own path do not cope with it: initdb could not find postgres.
pub fn plain(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    match text.strip_prefix(r"\\?\") {
        Some(rest) => PathBuf::from(rest),
        None => path.to_path_buf(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn none(_: &str) -> Option<String> {
        None
    }

    #[test]
    fn desk_043_data_home_matches_the_08_table_on_every_os() {
        let local =
            |k: &str| (k == "LOCALAPPDATA").then(|| r"C:\Users\a\AppData\Local".to_string());
        assert_eq!(
            data_home(Os::Windows, &local, r"C:\Users\a"),
            PathBuf::from(r"C:\Users\a\AppData\Local").join("OpenPhotoAgent")
        );
        assert_eq!(
            data_home(Os::MacOs, &none, "/Users/a"),
            PathBuf::from("/Users/a/Library/Application Support/OpenPhotoAgent")
        );
        // lower-case on Linux: dirs::data_local_dir() + NAME was a different folder from the CLI's
        assert_eq!(
            data_home(Os::Linux, &none, "/home/a"),
            PathBuf::from("/home/a/.local/share/openphotoagent")
        );
    }

    #[test]
    fn desk_049_verbatim_prefix_is_removed_from_bundled_paths() {
        assert_eq!(
            plain(Path::new(r"\\?\C:\Programs\OPA\pg\bin")),
            PathBuf::from(r"C:\Programs\OPA\pg\bin")
        );
        assert_eq!(
            plain(Path::new(r"\\?\UNC\nas\share\pg")),
            PathBuf::from(r"\\nas\share\pg")
        );
        assert_eq!(
            plain(Path::new("/opt/opa/pg")),
            PathBuf::from("/opt/opa/pg")
        );
    }

    #[test]
    fn desk_043_portable_mode_wins_and_empty_values_do_not_count() {
        let portable = |k: &str| (k == "OPAGENT_HOME").then(|| "/mnt/usb/opa".to_string());
        assert_eq!(
            data_home(Os::Linux, &portable, "/home/a"),
            PathBuf::from("/mnt/usb/opa")
        );
        let empty = |_: &str| Some(String::new());
        assert_eq!(
            data_home(Os::Linux, &empty, "/home/a"),
            PathBuf::from("/home/a/.local/share/openphotoagent")
        );
    }
}
