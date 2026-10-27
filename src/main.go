package main

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"
)

type object = map[string]any

type failure struct {
	code    string
	message string
}

func (e *failure) Error() string  { return e.message }
func fail(code, msg string) error { return &failure{code, msg} }
func code(err error) string {
	var f *failure
	if errors.As(err, &f) {
		return f.code
	}
	return ""
}
func str(v any) string       { s, _ := v.(string); return s }
func obj(v any) object       { m, _ := v.(map[string]any); return m }
func jsonBytes(v any) []byte { b, _ := json.Marshal(v); return b }
func hash(s string) string   { b := sha256.Sum256([]byte(s)); return hex.EncodeToString(b[:]) }
func secret(n int) (string, error) {
	b := make([]byte, n)
	if _, e := rand.Read(b); e != nil {
		return "", e
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}
func clone(m object) object {
	out := object{}
	for k, v := range m {
		out[k] = v
	}
	return out
}
func env(key, fallback string) string {
	if s := os.Getenv(key); s != "" {
		return s
	}
	return fallback
}

var idPattern = regexp.MustCompile(`^[a-zA-Z0-9-]{1,80}$`)
var turnPattern = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,128}$`)
var keyPattern = regexp.MustCompile(`^[a-zA-Z0-9-]+$`)
var operations = map[string]bool{}

func init() {
	for _, op := range strings.Fields(`project_get node_create node_update resource_create resource_update resource_import resource_bind image_generate image_status image_inspect image_bind video_generate task_get task_cancel task_retry context_select chapter_prepare video_transition result_select import_estimate text_import novel_import episode_inspect episode_import import_get import_cancel import_resume import_select_chapters director_start director_retry director_resume director_get director_cancel generation_start export_start export_get image_models library_list library_manage canvas_edit media_edit video_configure media_preview generation_list queue_cancel generation_cancel_node import_steps import_budget import_retry_speech canvas_history canvas_restore story_reparse review_acknowledge`) {
		operations[op] = true
	}
}

type store struct{ root string }

func (s store) path(key string) (string, error) {
	if !keyPattern.MatchString(key) {
		return "", errors.New("无效的凭据标识")
	}
	return filepath.Join(s.root, key+".json"), nil
}
func (s store) get(key string) (object, error) {
	p, e := s.path(key)
	if e != nil {
		return nil, e
	}
	st, e := os.Lstat(p)
	if os.IsNotExist(e) {
		return nil, nil
	}
	if e != nil {
		return nil, e
	}
	if !st.Mode().IsRegular() || (runtime.GOOS != "windows" && st.Mode().Perm()&0077 != 0) {
		return nil, errors.New("凭据文件必须是私密的普通文件")
	}
	b, e := os.ReadFile(p)
	if e != nil {
		return nil, e
	}
	var v object
	e = json.Unmarshal(b, &v)
	return v, e
}
func (s store) set(key string, v object) error {
	p, e := s.path(key)
	if e != nil {
		return e
	}
	if e = os.MkdirAll(s.root, 0700); e != nil {
		return e
	}
	f, e := os.CreateTemp(s.root, ".xsl-*")
	if e != nil {
		return e
	}
	defer os.Remove(f.Name())
	if e = f.Chmod(0600); e != nil {
		f.Close()
		return e
	}
	b, e := json.Marshal(v)
	if e == nil {
		_, e = f.Write(b)
	}
	if ce := f.Close(); e == nil {
		e = ce
	}
	if e != nil {
		return e
	}
	return os.Rename(f.Name(), p)
}
func (s store) del(key string) error {
	p, e := s.path(key)
	if e != nil {
		return e
	}
	e = os.Remove(p)
	if os.IsNotExist(e) {
		return nil
	}
	return e
}

type client struct {
	api, site, key string
	store          store
	http           *http.Client
}

func parseOrigin(v string) (string, error) {
	u, e := url.Parse(v)
	if e != nil || u == nil || u.User != nil || u.Hostname() == "" || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.Fragment != "" {
		return "", fail("INVALID_ORIGIN", "服务地址无效")
	}
	if u.Scheme != "https" && !(u.Scheme == "http" && (u.Hostname() == "localhost" || u.Hostname() == "127.0.0.1" || u.Hostname() == "::1")) {
		return "", fail("INVALID_ORIGIN", "请使用 HTTPS 服务地址")
	}
	return u.Scheme + "://" + u.Host, nil
}
func newClient() (*client, error) {
	api, e := parseOrigin(env("XSL_DIRECTOR_API_ORIGIN", "https://api.xiaosuanli.com"))
	if e != nil {
		return nil, e
	}
	site, e := parseOrigin(env("XSL_DIRECTOR_SITE_ORIGIN", "https://www.xiaosuanli.com"))
	if e != nil {
		return nil, e
	}
	home, e := os.UserHomeDir()
	if e != nil {
		return nil, e
	}
	identity := string(jsonBytes([]string{api, site, "xiaosuanli-codex"}))
	return &client{api: api, site: site, key: "director-" + hash(identity), store: store{env("XSL_DIRECTOR_STATE_DIR", filepath.Join(home, ".config", "xiaosuanli-director", "codex"))}, http: &http.Client{Timeout: 60 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}
func (c *client) request(path string, grant object, body object, method, turn string) (object, error) {
	var reader io.Reader
	if body != nil {
		reader = strings.NewReader(string(jsonBytes(body)))
	}
	req, e := http.NewRequest(method, c.api+path, reader)
	if e != nil {
		return nil, e
	}
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	token := str(grant["accessToken"])
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	if turn != "" {
		req.Header.Set("X-Director-User-Turn", turn)
	}
	res, e := c.http.Do(req)
	if e != nil {
		return nil, fail("RESULT_UNKNOWN", "连接中断，写操作可能已受理；请查询原任务或使用原 requestId 恢复")
	}
	defer res.Body.Close()
	b, e := io.ReadAll(io.LimitReader(res.Body, 16<<20))
	if e != nil {
		return nil, fail("RESULT_UNKNOWN", "读取响应中断，请查询原任务")
	}
	var response object
	if json.Unmarshal(b, &response) != nil || response == nil {
		return nil, fail("INVALID_RESPONSE", "服务响应不可读取，请查询原请求")
	}
	n, _ := response["code"].(float64)
	if res.StatusCode < 200 || res.StatusCode >= 300 || n != 200 {
		if n == 0 {
			n = float64(res.StatusCode)
		}
		msg := "服务未完成请求，请查询原任务后重试"
		if n >= 400 && n < 500 && str(response["message"]) != "" {
			msg = str(response["message"])
		}
		if token != "" {
			msg = strings.ReplaceAll(msg, token, "[redacted]")
		}
		msg = regexp.MustCompile(`https?://\S+`).ReplaceAllString(msg, "[service address]")
		if len([]rune(msg)) > 500 {
			msg = string([]rune(msg)[:500])
		}
		return nil, fail(fmt.Sprint(int(n)), msg)
	}
	data, ok := response["data"].(map[string]any)
	if !ok {
		return object{"value": response["data"]}, nil
	}
	return data, nil
}
func (c *client) validate(g object) error {
	expiry, e := time.Parse(time.RFC3339Nano, str(g["expiresAt"]))
	scopes, ok := g["scopes"].([]any)
	project, projectOK := g["projectId"].(string)
	account := false
	for _, x := range scopes {
		if str(x) == "project:select" {
			account = true
		}
	}
	if g == nil || str(g["clientId"]) != "xiaosuanli-codex" || str(g["channel"]) != "codex" || str(g["accessToken"]) == "" || !ok || e != nil || !expiry.After(time.Now()) || !projectOK || !(idPattern.MatchString(project) || (project == "" && account && idPattern.MatchString(str(g["authorizationId"])))) {
		return fail("INVALID_GRANT", "授权身份、工程或有效期不匹配，请重新连接")
	}
	return nil
}
func (c *client) grant() (object, error) {
	selected, e := c.store.get(c.key)
	if e != nil {
		return nil, e
	}
	key := str(selected["key"])
	if !strings.HasPrefix(key, c.key+"-") {
		return nil, fail("LOGIN_REQUIRED", "请先完成小算狸账号授权")
	}
	g, e := c.store.get(key)
	if e != nil {
		return nil, e
	}
	if e = c.validate(g); e != nil {
		return nil, e
	}
	suffix := str(g["projectId"])
	if suffix == "" {
		suffix = "account-" + str(g["authorizationId"])
	}
	if str(g["apiOrigin"]) != c.api || str(g["siteOrigin"]) != c.site || key != c.key+"-"+suffix {
		return nil, fail("INVALID_GRANT", "授权服务或工程不匹配")
	}
	return g, nil
}
func (c *client) status() (object, error) {
	g, e := c.grant()
	if e == nil {
		return object{"connected": true, "clientId": g["clientId"], "channel": g["channel"], "projectId": g["projectId"], "scopes": g["scopes"], "expiresAt": g["expiresAt"]}, nil
	}
	if code(e) == "" {
		return nil, e
	}
	out := object{"connected": false, "clientId": "xiaosuanli-codex", "channel": "codex", "reason": code(e)}
	if code(e) == "LOGIN_REQUIRED" {
		p, err := c.store.get(c.key + "-pending-login")
		if err != nil {
			return nil, err
		}
		expires, _ := p["expiresAt"].(float64)
		if expires > float64(time.Now().UnixMilli()) {
			out["reason"] = "LOGIN_PENDING"
			if p["exchangeInterrupted"] == true {
				out["reason"] = "LOGIN_EXCHANGE_INTERRUPTED"
			}
			out["state"] = p["state"]
			out["expiresAt"] = time.UnixMilli(int64(expires)).UTC().Format(time.RFC3339Nano)
		}
	}
	return out, nil
}
func (c *client) authStart() (object, error) {
	verifier, e := secret(32)
	if e != nil {
		return nil, e
	}
	state, e := secret(24)
	if e != nil {
		return nil, e
	}
	expires := time.Now().Add(5 * time.Minute)
	if e = c.store.set(c.key+"-pending-login", object{"verifier": verifier, "state": state, "expiresAt": expires.UnixMilli()}); e != nil {
		return nil, e
	}
	digest := sha256.Sum256([]byte(verifier))
	q := url.Values{"clientId": {"xiaosuanli-codex"}, "codeChallenge": {base64.RawURLEncoding.EncodeToString(digest[:])}, "codeChallengeMethod": {"S256"}, "state": {state}}
	return object{"url": c.site + "/authorize/director?" + q.Encode(), "state": state, "expiresAt": expires.UTC().Format(time.RFC3339Nano)}, nil
}
func (c *client) authComplete(input object) (object, error) {
	key := c.key + "-pending-login"
	p, e := c.store.get(key)
	if e != nil {
		return nil, e
	}
	expires, _ := p["expiresAt"].(float64)
	if p == nil || str(p["state"]) != str(input["state"]) || expires <= float64(time.Now().UnixMilli()) || strings.TrimSpace(str(input["code"])) == "" {
		return nil, fail("INVALID_LOGIN", "授权已过期或不匹配，请重新发起")
	}
	g, e := c.request("/api/subscriber/authorizations/token", nil, object{"clientId": "xiaosuanli-codex", "code": strings.TrimSpace(str(input["code"])), "codeVerifier": p["verifier"]}, "POST", "")
	if e == nil {
		e = c.validate(g)
	}
	if e != nil {
		if code(e) == "RESULT_UNKNOWN" || code(e) == "INVALID_RESPONSE" {
			p["exchangeInterrupted"] = true
			if se := c.store.set(key, p); se != nil {
				return nil, se
			}
		} else {
			_ = c.store.del(key)
		}
		return nil, e
	}
	suffix := str(g["projectId"])
	if suffix == "" {
		suffix = "account-" + str(g["authorizationId"])
	}
	grantKey := c.key + "-" + suffix
	g["apiOrigin"] = c.api
	g["siteOrigin"] = c.site
	if e = c.store.set(grantKey, g); e != nil {
		return nil, e
	}
	if e = c.store.set(c.key, object{"key": grantKey}); e != nil {
		return nil, e
	}
	if e = c.store.del(key); e != nil {
		return nil, e
	}
	status, e := c.status()
	if e != nil {
		return nil, e
	}
	projects, e := c.projects()
	if e != nil {
		return nil, e
	}
	status["initialization"] = projects
	status["next"] = "让用户选择工程，再列出章节"
	return status, nil
}
func (c *client) projects() (object, error) {
	g, e := c.grant()
	if e != nil {
		return nil, e
	}
	return c.request("/api/subscriber/automated-director/projects", g, nil, "GET", "")
}
func (c *client) chapters(project string) (object, error) {
	g, e := c.grant()
	if e != nil {
		return nil, e
	}
	if project == "" {
		project = str(g["projectId"])
	}
	if !idPattern.MatchString(project) || (str(g["projectId"]) != "" && str(g["projectId"]) != project) {
		return nil, fail("PROJECT_NOT_AUTHORIZED", "请选择已授权工程")
	}
	return c.request("/api/subscriber/automated-director/projects/"+project+"/chapters", g, nil, "GET", "")
}
func (c *client) conversationKey(id string) (string, error) {
	if !turnPattern.MatchString(id) {
		return "", fail("CONVERSATION_REQUIRED", "请在具体对话中打开章节")
	}
	return c.key + "-conversation-" + hash(id), nil
}
func (c *client) conversationGrant(id string) (object, error) {
	key, e := c.conversationKey(id)
	if e != nil {
		return nil, e
	}
	g, e := c.store.get(key)
	if e != nil {
		return nil, e
	}
	if g == nil {
		return nil, fail("CHAPTER_NOT_OPEN", "请先在当前对话打开工程和章节")
	}
	if e = c.validate(g); e != nil {
		return nil, e
	}
	if str(g["conversationId"]) != id || str(g["chapterId"]) == "" || str(g["apiOrigin"]) != c.api || str(g["siteOrigin"]) != c.site {
		return nil, fail("INVALID_GRANT", "对话章节绑定不匹配")
	}
	return g, nil
}
func (c *client) editor(project string) string {
	return c.site + "/apps/automated-director?" + url.Values{"project": {project}}.Encode()
}
func (c *client) context(id string) (object, error) {
	g, e := c.conversationGrant(id)
	if e != nil {
		return nil, e
	}
	state, e := c.request("/api/subscriber/automated-director/capabilities", g, nil, "GET", "")
	if e != nil {
		return nil, e
	}
	if str(state["projectId"]) != str(g["projectId"]) || str(state["chapterId"]) != str(g["chapterId"]) || str(state["sessionId"]) != str(g["authorizationId"]) {
		return nil, fail("INVALID_CAPABILITIES", "对话状态与固定章节不匹配")
	}
	return object{"connected": true, "clientId": g["clientId"], "channel": g["channel"], "projectId": g["projectId"], "chapterId": g["chapterId"], "conversationId": id, "editorUrl": c.editor(str(g["projectId"])), "canWrite": state["canWrite"] == true, "userTurnId": state["userTurnId"]}, nil
}
func (c *client) open(id, chapter, project string) (object, error) {
	key, e := c.conversationKey(id)
	if e != nil {
		return nil, e
	}
	existing, e := c.store.get(key)
	if e != nil {
		return nil, e
	}
	if existing != nil {
		if str(existing["chapterId"]) != chapter || (project != "" && str(existing["projectId"]) != project) {
			return nil, fail("CHAPTER_ALREADY_BOUND", "当前对话已固定到另一章节，请新建对话")
		}
		return c.context(id)
	}
	parent, e := c.grant()
	if e != nil {
		return nil, e
	}
	if project == "" {
		project = str(parent["projectId"])
	}
	g, e := c.request("/api/subscriber/automated-director/conversations/open", parent, object{"conversationId": id, "chapterId": chapter, "projectId": project}, "POST", "")
	if e != nil {
		return nil, e
	}
	if e = c.validate(g); e != nil {
		return nil, e
	}
	if str(g["projectId"]) != project || str(g["chapterId"]) != chapter || str(g["conversationId"]) != id {
		return nil, fail("INVALID_GRANT", "服务端对话绑定不匹配")
	}
	g["apiOrigin"] = c.api
	g["siteOrigin"] = c.site
	if e = c.store.set(key, g); e != nil {
		return nil, e
	}
	return c.context(id)
}
func (c *client) beginTurn(id, turn string) error {
	if !turnPattern.MatchString(turn) {
		return fail("USER_TURN_REQUIRED", "缺少真实用户消息标识")
	}
	g, e := c.conversationGrant(id)
	if code(e) == "CHAPTER_NOT_OPEN" {
		g, e = c.grant()
	}
	if e != nil {
		return e
	}
	_, e = c.request("/api/subscriber/automated-director/conversations/user-turn", g, object{"userTurnId": turn, "conversationId": id}, "POST", "")
	return e
}
func (c *client) create(kind string, input object, id string) (object, error) {
	key, e := c.conversationKey(id)
	if e != nil {
		return nil, e
	}
	existing, e := c.store.get(key)
	if e != nil {
		return nil, e
	}
	if existing != nil {
		return nil, fail("CHAPTER_ALREADY_BOUND", "当前对话已固定章节，请新建对话后创建")
	}
	allowed := map[string]bool{"title": true, "requestId": true}
	if kind == "chapter" {
		allowed["projectId"] = true
		allowed["scriptId"] = true
		allowed["expectedVersion"] = true
	}
	for k := range input {
		if !allowed[k] {
			return nil, fail("INVALID_ARGUMENTS", "创建参数无效")
		}
	}
	g, e := c.grant()
	if e != nil {
		return nil, e
	}
	project := str(input["projectId"])
	suffix := "/projects"
	if kind == "chapter" {
		if !idPattern.MatchString(project) || (str(g["projectId"]) != "" && str(g["projectId"]) != project) {
			return nil, fail("PROJECT_NOT_AUTHORIZED", "请选择已授权工程")
		}
		suffix += "/" + project + "/chapters"
	}
	body := clone(input)
	body["conversationId"] = id
	out, e := c.request("/api/subscriber/automated-director"+suffix, g, body, "POST", "")
	if e != nil {
		return nil, e
	}
	if p := str(obj(out["project"])["id"]); p != "" {
		project = p
	}
	out["editorUrl"] = c.editor(project)
	return out, nil
}
func (c *client) capabilities() (object, error) {
	g, e := c.grant()
	if e != nil {
		return nil, e
	}
	out, e := c.request("/api/subscriber/automated-director/capabilities", g, nil, "GET", "")
	if e != nil {
		return nil, e
	}
	tools, ok := out["tools"].([]any)
	if !ok || str(out["clientId"]) != str(g["clientId"]) || str(out["channel"]) != str(g["channel"]) || str(out["projectId"]) != str(g["projectId"]) {
		return nil, fail("INVALID_CAPABILITIES", "服务能力与当前授权不匹配")
	}
	filtered := []any{}
	for _, v := range tools {
		t := obj(v)
		if operations[str(t["operation"])] && str(obj(t["inputSchema"])["type"]) == "object" {
			filtered = append(filtered, t)
		}
	}
	out["tools"] = filtered
	return out, nil
}
func (c *client) execute(op string, args object, id, turn string) (object, error) {
	if !operations[op] || args == nil {
		return nil, fail("INVALID_ARGUMENTS", "业务参数无效")
	}
	g, e := c.conversationGrant(id)
	if e != nil {
		return nil, e
	}
	if p, ok := args["projectId"]; ok && str(p) != str(g["projectId"]) {
		return nil, fail("INVALID_ARGUMENTS", "工程不匹配")
	}
	for _, k := range []string{"userId", "clientId", "channel", "conversationId", "userTurnId", "billing", "token", "url"} {
		if _, ok := args[k]; ok {
			return nil, fail("INVALID_ARGUMENTS", "身份与计费由服务端确定")
		}
	}
	body := clone(args)
	body["projectId"] = g["projectId"]
	return c.request("/api/subscriber/automated-director/tools/"+op, g, body, "POST", turn)
}
func (c *client) logout() (object, error) {
	if e := c.store.del(c.key + "-pending-login"); e != nil {
		return nil, e
	}
	selected, e := c.store.get(c.key)
	if e != nil {
		return nil, e
	}
	if selected == nil {
		return object{"connected": false}, nil
	}
	g, e := c.grant()
	if e == nil {
		_, e = c.request("/api/subscriber/authorizations/current", g, nil, "DELETE", "")
	}
	if e != nil && code(e) != "401" && code(e) != "INVALID_GRANT" && code(e) != "LOGIN_REQUIRED" {
		return nil, e
	}
	key := str(selected["key"])
	if strings.HasPrefix(key, c.key+"-") {
		if e = c.store.del(key); e != nil {
			return nil, e
		}
	}
	if e = c.store.del(c.key); e != nil {
		return nil, e
	}
	return object{"connected": false}, nil
}
