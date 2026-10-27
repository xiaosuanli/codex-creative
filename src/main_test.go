package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestAuthorizationChapterAndTurn(t *testing.T) {
	var actualTurn string
	var captured object
	mux := http.NewServeMux()
	grant := func(project string) object {
		return object{"clientId": "xiaosuanli-codex", "channel": "codex", "accessToken": "secret-test-token", "projectId": project, "authorizationId": "auth-1", "scopes": []any{"project:select", "project:write"}, "expiresAt": time.Now().Add(time.Hour).UTC().Format(time.RFC3339)}
	}
	mux.HandleFunc("/api/subscriber/authorizations/token", func(w http.ResponseWriter, r *http.Request) {
		var body object
		_ = json.NewDecoder(r.Body).Decode(&body)
		if str(body["codeVerifier"]) == "" || str(body["code"]) != "once" {
			t.Error("missing PKCE exchange")
		}
		_ = json.NewEncoder(w).Encode(object{"code": 200, "data": grant("")})
	})
	mux.HandleFunc("/api/subscriber/automated-director/projects", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(object{"code": 200, "data": object{"projects": []any{}}})
	})
	mux.HandleFunc("/api/subscriber/automated-director/conversations/open", func(w http.ResponseWriter, r *http.Request) {
		g := grant("project-1")
		g["chapterId"] = "chapter-1"
		g["conversationId"] = "chat-1"
		_ = json.NewEncoder(w).Encode(object{"code": 200, "data": g})
	})
	mux.HandleFunc("/api/subscriber/automated-director/capabilities", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(object{"code": 200, "data": object{"projectId": "project-1", "chapterId": "chapter-1", "sessionId": "auth-1", "canWrite": true}})
	})
	mux.HandleFunc("/api/subscriber/automated-director/conversations/user-turn", func(w http.ResponseWriter, r *http.Request) {
		var body object
		_ = json.NewDecoder(r.Body).Decode(&body)
		actualTurn = str(body["userTurnId"])
		_ = json.NewEncoder(w).Encode(object{"code": 200, "data": object{}})
	})
	mux.HandleFunc("/api/subscriber/automated-director/tools/project_get", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Director-User-Turn") != "turn-1" {
			t.Error("turn header lost")
		}
		_ = json.NewDecoder(r.Body).Decode(&captured)
		_ = json.NewEncoder(w).Encode(object{"code": 200, "data": object{"ok": true}})
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()
	t.Setenv("XSL_DIRECTOR_API_ORIGIN", srv.URL)
	t.Setenv("XSL_DIRECTOR_SITE_ORIGIN", srv.URL)
	t.Setenv("XSL_DIRECTOR_STATE_DIR", t.TempDir())
	c, e := newClient()
	if e != nil {
		t.Fatal(e)
	}
	start, e := c.authStart()
	if e != nil {
		t.Fatal(e)
	}
	if _, e = c.authComplete(object{"code": "once", "state": start["state"]}); e != nil {
		t.Fatal(e)
	}
	if _, e = c.open("chat-1", "chapter-1", "project-1"); e != nil {
		t.Fatal(e)
	}
	if e = c.beginTurn("chat-1", "turn-1"); e != nil {
		t.Fatal(e)
	}
	if actualTurn != "turn-1" {
		t.Fatal(actualTurn)
	}
	if _, e = c.execute("project_get", object{}, "chat-1", "turn-1"); e != nil {
		t.Fatal(e)
	}
	if str(captured["projectId"]) != "project-1" {
		t.Fatal(captured)
	}
	if _, e = c.execute("project_get", object{"projectId": "other"}, "chat-1", "turn-1"); code(e) != "INVALID_ARGUMENTS" {
		t.Fatalf("cross-project: %v", e)
	}
	if _, e = c.open("chat-1", "chapter-2", "project-1"); code(e) != "CHAPTER_ALREADY_BOUND" {
		t.Fatalf("chapter switch: %v", e)
	}
}

func TestHookBindsOriginalTurn(t *testing.T) {
	t.Setenv("XSL_DIRECTOR_STATE_DIR", t.TempDir())
	c, e := newClient()
	if e != nil {
		t.Fatal(e)
	}
	input := object{"hook_event_name": "UserPromptSubmit", "session_id": "chat-1", "turn_id": "turn-1"}
	if _, e = handleHook(input); e != nil {
		t.Fatal(e)
	}
	input = object{"hook_event_name": "PreToolUse", "session_id": "chat-1", "turn_id": "turn-1", "tool_name": "exec_command", "tool_input": object{"cmd": "/some/xiaosuanli-director/bin/xsl-director-linux-amd64 execute"}}
	out, e := handleHook(input)
	if e != nil {
		t.Fatal(e)
	}
	detail := obj(out["hookSpecificOutput"])
	if detail["permissionDecision"] != "allow" || !strings.Contains(str(obj(detail["updatedInput"])["cmd"]), contextKey("chat-1", "turn-1")) {
		t.Fatal(out)
	}
	t.Setenv("XSL_DIRECTOR_CONTEXT_KEY", contextKey("chat-1", "turn-1"))
	if _, e = readContext(c); e != nil {
		t.Fatal(e)
	}
	input["turn_id"] = "turn-2"
	out, e = handleHook(input)
	if e != nil || obj(out["hookSpecificOutput"])["permissionDecision"] != "deny" {
		t.Fatalf("stale turn accepted: %v %v", out, e)
	}
	files, e := os.ReadDir(c.store.root)
	if e != nil {
		t.Fatal(e)
	}
	for _, f := range files {
		b, e := os.ReadFile(filepath.Join(c.store.root, f.Name()))
		if e != nil {
			t.Fatal(e)
		}
		if strings.Contains(string(b), "prompt") {
			t.Fatal("prompt persisted")
		}
	}
}
