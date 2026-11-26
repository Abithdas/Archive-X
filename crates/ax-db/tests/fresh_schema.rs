//! A database created from `schema.sql` must have every table and column of the snapshot Python's own `PRAGMA table_info` produced
//! (`schema.json`, made by a separate generator), plus the objects and starting rows of the SQL itself.

use ax_db::rusqlite::Connection;
use serde_json::Value;

fn fresh() -> Connection {
    let c = Connection::open_in_memory().unwrap();
    ax_db::schema::create_fresh(&c).unwrap();
    c
}

#[test]
fn every_table_and_column_of_the_python_snapshot_exists_with_its_type_and_keys() {
    let c = fresh();
    let snapshot: Value = serde_json::from_str(include_str!("../schema/schema.json")).unwrap();
    let mut tables = 0;
    for (table, cols) in snapshot.as_object().unwrap() {
        let mut st = c.prepare(&format!("PRAGMA table_info(\"{table}\")")).unwrap();
        let got: Vec<(String, String, bool, bool)> =
            st.query_map([], |r| Ok((r.get(1)?, r.get(2)?, r.get::<_, i64>(3)? != 0, r.get::<_, i64>(5)? != 0))).unwrap().map(|r| r.unwrap()).collect();
        let want: Vec<(String, String, bool, bool)> = cols
            .as_array()
            .unwrap()
            .iter()
            .map(|col| {
                (
                    col["name"].as_str().unwrap().to_string(),
                    col["type"].as_str().unwrap().to_string(),
                    col["notnull"].as_bool().unwrap(),
                    col["pk"].as_bool().unwrap(),
                )
            })
            .collect();
        assert_eq!(got, want, "table {table}");
        tables += 1;
    }
    assert!(tables > 100, "{tables} tables");
}

#[test]
fn no_table_is_missing_or_extra() {
    let c = fresh();
    let snapshot: Value = serde_json::from_str(include_str!("../schema/schema.json")).unwrap();
    let mut st = c.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%\\_fts\\_%' ESCAPE '\\' AND name NOT LIKE '%\\_fts' ESCAPE '\\' ORDER BY name").unwrap();
    let got: Vec<String> = st.query_map([], |r| r.get(0)).unwrap().map(|r| r.unwrap()).collect();
    let want: Vec<String> = snapshot.as_object().unwrap().keys().filter(|k| !k.contains("_fts")).cloned().collect();
    let mut want = want;
    want.sort();
    assert_eq!(got, want);
}

#[test]
fn indexes_triggers_views_and_the_full_text_tables_are_there() {
    let c = fresh();
    let count = |kind: &str| c.query_row("SELECT COUNT(*) FROM sqlite_master WHERE type=?", [kind], |r| r.get::<_, i64>(0)).unwrap();
    assert_eq!((count("view"), count("trigger")), (4, 75));
    assert!(count("index") >= 190, "{} indexes", count("index"));
    for t in ["media_fts", "performer_fts", "tag_fts", "studio_fts"] {
        // the index tables work: an insert and a prefix search
        let column = if t == "media_fts" { "title" } else { "name" };
        c.execute(&format!("INSERT INTO {t}(rowid, {column}) VALUES (1, 'hello world')"), []).unwrap();
        let n: i64 = c.query_row(&format!("SELECT COUNT(*) FROM {t} WHERE {t} MATCH 'hel*'"), [], |r| r.get(0)).unwrap();
        assert_eq!(n, 1, "{t}");
    }
}

#[test]
fn the_starting_rows_are_the_ones_python_leaves() {
    let c = fresh();
    let settings: Vec<(String, String)> = c
        .prepare("SELECT key, value FROM settings ORDER BY key")
        .unwrap()
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
        .unwrap()
        .map(|r| r.unwrap())
        .collect();
    assert_eq!(
        settings,
        [
            ("archive_x_delivery_reconcile_v168".to_string(), "1".to_string()),
            ("archive_x_peer_identity_snapshots_v197".to_string(), "1".to_string()),
            ("ax_library_ready_v1".to_string(), "1".to_string()),
            ("search_index_version".to_string(), "\"search-index-v2\"".to_string()),
            ("v18.25_ambiguous_title_repair_done".to_string(), "1".to_string()),
        ]
    );
    let versions: Vec<(String, i64)> = c
        .prepare("SELECT scope, version FROM data_versions ORDER BY scope")
        .unwrap()
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
        .unwrap()
        .map(|r| r.unwrap())
        .collect();
    assert_eq!(versions, [("channels".to_string(), 0), ("entities".to_string(), 0), ("settings".to_string(), 2), ("videos".to_string(), 0)]);
}

#[test]
fn a_database_that_already_has_tables_is_refused() {
    let c = fresh();
    assert!(ax_db::schema::create_fresh(&c).is_err());
    let d = Connection::open_in_memory().unwrap();
    d.execute_batch("CREATE TABLE x(a)").unwrap();
    assert!(ax_db::schema::create_fresh(&d).is_err());
}
