package fsbot

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"time"

	_ "modernc.org/sqlite"
)

const schema = `
CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY,allowed INTEGER NOT NULL DEFAULT 0,tz TEXT,token TEXT,token_secret TEXT,link_valid INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS drafts (draft_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,payload TEXT NOT NULL,created_at INTEGER NOT NULL,writing INTEGER NOT NULL DEFAULT 0,writing_since INTEGER);
CREATE TABLE IF NOT EXISTS batches (batch_id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,entry_ids TEXT NOT NULL,created_at INTEGER NOT NULL,draft_id INTEGER,undoing INTEGER NOT NULL DEFAULT 0,undoing_since INTEGER);
CREATE TABLE IF NOT EXISTS barcode_bindings (user_id INTEGER NOT NULL,barcode TEXT NOT NULL,food_id TEXT NOT NULL,PRIMARY KEY(user_id,barcode));`
const claimTTL = 3600

type User struct {
	ID                 int64
	Allowed, LinkValid bool
	TZ, Token, Secret  string
}

func (u User) Linked() bool { return u.Token != "" && u.Secret != "" && u.LinkValid }

type Store struct{ db *sql.DB }

func OpenStore(path string) (*Store, error) {
	if e := os.MkdirAll(filepath.Dir(path), 0700); e != nil {
		return nil, e
	}
	absolute, e := filepath.Abs(path)
	if e != nil {
		return nil, e
	}
	dsn := (&url.URL{Scheme: "file", Path: absolute}).String() + "?_txlock=immediate&_busy_timeout=5000"
	db, e := sql.Open("sqlite", dsn)
	if e != nil {
		return nil, e
	}
	db.SetMaxOpenConns(1)
	s := &Store{db}
	if e = s.init(); e != nil {
		db.Close()
		return nil, fmt.Errorf("не удалось открыть базу %s: %w", path, e)
	}
	return s, nil
}
func (s *Store) init() error {
	if _, e := s.db.Exec(`PRAGMA busy_timeout=5000`); e != nil {
		return e
	}
	if _, e := s.db.Exec(schema); e != nil {
		return e
	}
	for table, columns := range map[string]map[string]string{"drafts": {"writing": "INTEGER NOT NULL DEFAULT 0", "writing_since": "INTEGER"}, "batches": {"draft_id": "INTEGER", "undoing": "INTEGER NOT NULL DEFAULT 0", "undoing_since": "INTEGER"}} {
		rows, e := s.db.Query("PRAGMA table_info(" + table + ")")
		if e != nil {
			return e
		}
		present := map[string]bool{}
		for rows.Next() {
			var cid, notnull, pk int
			var name, typ string
			var def any
			if e = rows.Scan(&cid, &name, &typ, &notnull, &def, &pk); e != nil {
				rows.Close()
				return e
			}
			present[name] = true
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return e
		}
		for name, typ := range columns {
			if !present[name] {
				if _, e = s.db.Exec("ALTER TABLE " + table + " ADD COLUMN " + name + " " + typ); e != nil {
					return e
				}
			}
		}
	}
	for _, q := range []string{`CREATE UNIQUE INDEX IF NOT EXISTS batches_draft_id ON batches(draft_id)`, `DELETE FROM batches WHERE entry_ids = '[]'`} {
		if _, e := s.db.Exec(q); e != nil {
			return e
		}
	}
	for _, q := range []string{`UPDATE drafts SET writing=0,writing_since=NULL WHERE writing=1 AND (writing_since IS NULL OR writing_since < ?)`, `UPDATE batches SET undoing=0,undoing_since=NULL WHERE undoing=1 AND (undoing_since IS NULL OR undoing_since < ?)`} {
		if _, e := s.db.Exec(q, time.Now().Unix()-claimTTL); e != nil {
			return e
		}
	}
	return nil
}
func (s *Store) Close() error { return s.db.Close() }
func (s *Store) EnsureUser(id int64) (*User, error) {
	_, e := s.db.Exec(`INSERT OR IGNORE INTO users(user_id,created_at) VALUES(?,?)`, id, time.Now().Unix())
	if e != nil {
		return nil, e
	}
	return s.User(id)
}
func (s *Store) User(id int64) (*User, error) {
	u := &User{}
	e := s.db.QueryRow(`SELECT user_id,allowed,COALESCE(tz,''),COALESCE(token,''),COALESCE(token_secret,''),link_valid FROM users WHERE user_id=?`, id).Scan(&u.ID, &u.Allowed, &u.TZ, &u.Token, &u.Secret, &u.LinkValid)
	if errors.Is(e, sql.ErrNoRows) {
		return nil, nil
	}
	return u, e
}
func (s *Store) Allow(id int64) error {
	_, e := s.db.Exec(`INSERT INTO users(user_id,allowed,created_at) VALUES(?,1,?) ON CONFLICT(user_id) DO UPDATE SET allowed=1`, id, time.Now().Unix())
	return e
}
func (s *Store) SaveLink(id int64, token, secret string) error {
	_, e := s.db.Exec(`UPDATE users SET token=?,token_secret=?,link_valid=1 WHERE user_id=?`, token, secret, id)
	return e
}
func (s *Store) InvalidateLink(id int64) error {
	_, e := s.db.Exec(`UPDATE users SET link_valid=0 WHERE user_id=?`, id)
	return e
}
func (s *Store) SetTZ(id int64, tz string) error {
	_, e := s.db.Exec(`UPDATE users SET tz=? WHERE user_id=?`, tz, id)
	return e
}
func (s *Store) SaveDraft(user int64, d *Draft) (int64, error) {
	b, e := json.Marshal(d)
	if e != nil {
		return 0, e
	}
	r, e := s.db.Exec(`INSERT INTO drafts(user_id,payload,created_at) VALUES(?,?,?)`, user, string(b), time.Now().Unix())
	if e != nil {
		return 0, e
	}
	return r.LastInsertId()
}
func (s *Store) UpdateDraft(id, user int64, d *Draft) error {
	b, e := json.Marshal(d)
	if e != nil {
		return e
	}
	r, e := s.db.Exec(`UPDATE drafts SET payload=? WHERE draft_id=? AND user_id=?`, string(b), id, user)
	if e != nil {
		return e
	}
	n, e := r.RowsAffected()
	if e == nil && n != 1 {
		return fmt.Errorf("черновик уже неактуален")
	}
	return e
}
func decodeDraft(row *sql.Row) (*Draft, error) {
	var b string
	e := row.Scan(&b)
	if errors.Is(e, sql.ErrNoRows) {
		return nil, nil
	}
	if e != nil {
		return nil, e
	}
	var d Draft
	e = json.Unmarshal([]byte(b), &d)
	return &d, e
}
func (s *Store) Draft(id, user int64) (*Draft, error) {
	return decodeDraft(s.db.QueryRow(`SELECT payload FROM drafts WHERE draft_id=? AND user_id=?`, id, user))
}
func (s *Store) ClaimDraft(id, user int64) (*Draft, error) {
	now := time.Now().Unix()
	return decodeDraft(s.db.QueryRow(`UPDATE drafts SET writing=1,writing_since=? WHERE draft_id=? AND user_id=? AND (writing=0 OR writing_since IS NULL OR writing_since < ?) AND NOT EXISTS(SELECT 1 FROM batches WHERE batches.draft_id=drafts.draft_id AND batches.undoing=1) RETURNING payload`, now, id, user, now-claimTTL))
}
func (s *Store) ReleaseDraft(id, user int64) error {
	_, e := s.db.Exec(`UPDATE drafts SET writing=0,writing_since=NULL WHERE draft_id=? AND user_id=?`, id, user)
	return e
}
func (s *Store) DeleteDraft(id, user int64) error {
	_, e := s.db.Exec(`DELETE FROM drafts WHERE draft_id=? AND user_id=?`, id, user)
	return e
}
func (s *Store) LastDraft(user int64) (int64, *Draft, error) {
	var id int64
	var b string
	e := s.db.QueryRow(`SELECT draft_id,payload FROM drafts WHERE user_id=? ORDER BY draft_id DESC LIMIT 1`, user).Scan(&id, &b)
	if errors.Is(e, sql.ErrNoRows) {
		return 0, nil, nil
	}
	if e != nil {
		return 0, nil, e
	}
	var d Draft
	e = json.Unmarshal([]byte(b), &d)
	return id, &d, e
}
func (s *Store) BoundFood(user int64, code string) (string, error) {
	var id string
	e := s.db.QueryRow(`SELECT food_id FROM barcode_bindings WHERE user_id=? AND barcode=?`, user, code).Scan(&id)
	if errors.Is(e, sql.ErrNoRows) {
		return "", nil
	}
	return id, e
}
func (s *Store) BindBarcode(user int64, code, id string) error {
	_, e := s.db.Exec(`INSERT INTO barcode_bindings(user_id,barcode,food_id) VALUES(?,?,?) ON CONFLICT(user_id,barcode) DO UPDATE SET food_id=excluded.food_id`, user, code, id)
	return e
}
func (s *Store) RecordWrite(id, user int64, d *Draft, entry string) error {
	b, e := json.Marshal(d)
	if e != nil {
		return e
	}
	tx, e := s.db.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	var raw string
	var ids []string
	e = tx.QueryRow(`SELECT entry_ids FROM batches WHERE draft_id=? AND user_id=?`, id, user).Scan(&raw)
	if e != nil && !errors.Is(e, sql.ErrNoRows) {
		return e
	}
	if raw != "" {
		if e = json.Unmarshal([]byte(raw), &ids); e != nil {
			return e
		}
	}
	found := false
	for _, v := range ids {
		found = found || v == entry
	}
	if !found {
		ids = append(ids, entry)
	}
	entries, e := json.Marshal(ids)
	if e != nil {
		return e
	}
	if _, e = tx.Exec(`INSERT INTO batches(user_id,entry_ids,created_at,draft_id) VALUES(?,?,?,?) ON CONFLICT(draft_id) DO UPDATE SET entry_ids=excluded.entry_ids`, user, string(entries), time.Now().Unix(), id); e != nil {
		return e
	}
	r, e := tx.Exec(`UPDATE drafts SET payload=? WHERE draft_id=? AND user_id=?`, string(b), id, user)
	if e != nil {
		return e
	}
	n, e := r.RowsAffected()
	if e != nil {
		return e
	}
	if n != 1 {
		return fmt.Errorf("черновик уже неактуален")
	}
	return tx.Commit()
}
func (s *Store) ClaimBatch(user int64) (int64, []string, error) {
	now := time.Now().Unix()
	var id int64
	var b string
	e := s.db.QueryRow(`UPDATE batches SET undoing=1,undoing_since=? WHERE batch_id=(SELECT batch_id FROM batches WHERE user_id=? ORDER BY batch_id DESC LIMIT 1) AND (undoing=0 OR undoing_since IS NULL OR undoing_since < ?) AND NOT EXISTS(SELECT 1 FROM drafts WHERE drafts.draft_id=batches.draft_id AND drafts.writing=1) RETURNING batch_id,entry_ids`, now, user, now-claimTTL).Scan(&id, &b)
	if errors.Is(e, sql.ErrNoRows) {
		return 0, nil, nil
	}
	if e != nil {
		return 0, nil, e
	}
	var ids []string
	e = json.Unmarshal([]byte(b), &ids)
	return id, ids, e
}
func (s *Store) ReleaseBatch(id, user int64) error {
	tx, e := s.db.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	if _, e = tx.Exec(`DELETE FROM batches WHERE batch_id=? AND user_id=? AND entry_ids='[]'`, id, user); e != nil {
		return e
	}
	if _, e = tx.Exec(`UPDATE batches SET undoing=0,undoing_since=NULL WHERE batch_id=? AND user_id=?`, id, user); e != nil {
		return e
	}
	return tx.Commit()
}
func (s *Store) RecordUndo(id, user int64, entry string) error {
	tx, e := s.db.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	var draft sql.NullInt64
	var raw string
	e = tx.QueryRow(`SELECT draft_id,entry_ids FROM batches WHERE batch_id=? AND user_id=? AND undoing=1`, id, user).Scan(&draft, &raw)
	if e != nil {
		return e
	}
	var ids []string
	if e = json.Unmarshal([]byte(raw), &ids); e != nil {
		return e
	}
	remaining := []string{}
	found := false
	for _, v := range ids {
		if v == entry {
			found = true
		} else {
			remaining = append(remaining, v)
		}
	}
	if !found {
		return fmt.Errorf("запись уже удалена из пачки")
	}
	if draft.Valid {
		d, e := decodeDraft(tx.QueryRow(`SELECT payload FROM drafts WHERE draft_id=? AND user_id=?`, draft.Int64, user))
		if e != nil {
			return e
		}
		if d != nil {
			for _, i := range d.Items {
				if i.EntryID == entry {
					i.Status = "undone"
				}
			}
			b, e := json.Marshal(d)
			if e != nil {
				return e
			}
			if _, e = tx.Exec(`UPDATE drafts SET payload=? WHERE draft_id=? AND user_id=?`, string(b), draft.Int64, user); e != nil {
				return e
			}
		}
	}
	b, e := json.Marshal(remaining)
	if e != nil {
		return e
	}
	if _, e = tx.Exec(`UPDATE batches SET entry_ids=? WHERE batch_id=? AND user_id=?`, string(b), id, user); e != nil {
		return e
	}
	return tx.Commit()
}
