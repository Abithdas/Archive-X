use std::collections::BTreeMap;
use std::path::PathBuf;

/// Everything `config.py` reads from the machine: environment variables, the home and working folders, the application folder.
/// Tests build one by hand; [`Env::from_process`] reads the real values.
#[derive(Debug, Clone)]
pub struct Env {
    vars: BTreeMap<String, String>,
    pub home: PathBuf,
    pub cwd: PathBuf,
    /// The folder the program is installed in (`config.APP_DIR`): only legacy data is looked for here, and a new data folder may not go inside it.
    pub app_dir: PathBuf,
    /// Windows paths and defaults (`os.name == "nt"`).
    pub windows: bool,
    /// `config.APP_VERSION`: "dev" from a checkout, the release number in a build.
    pub app_version: String,
}

impl Env {
    pub fn new(vars: impl IntoIterator<Item = (String, String)>, home: PathBuf, cwd: PathBuf, app_dir: PathBuf) -> Env {
        Env { vars: vars.into_iter().collect(), home, cwd, app_dir, windows: cfg!(windows), app_version: "dev".into() }
    }

    pub fn from_process(app_dir: PathBuf) -> std::io::Result<Env> {
        #[allow(deprecated)]
        let home = std::env::home_dir().unwrap_or_default();
        Ok(Env::new(std::env::vars(), home, std::env::current_dir()?, app_dir))
    }

    /// `os.getenv(name)`.
    pub fn get(&self, name: &str) -> Option<&str> {
        self.vars.get(name).map(String::as_str)
    }

    /// `os.getenv(name) or ""`-style reads: a variable that is set but empty counts as not set.
    pub fn non_empty(&self, name: &str) -> Option<&str> {
        self.get(name).filter(|v| !v.is_empty())
    }

    pub fn set(&mut self, name: &str, value: &str) {
        self.vars.insert(name.to_string(), value.to_string());
    }

    pub fn now_utc_iso(&self) -> String {
        crate::clock::utc_iso_now()
    }

    pub fn local_stamp(&self) -> String {
        crate::clock::local_stamp_now()
    }
}
