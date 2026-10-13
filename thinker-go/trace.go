package thinker

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// SnapshotStore publishes complete objects without overwriting existing keys.
// Hosts can implement this with an S3 or GCS client and conditional creation.
type SnapshotStore interface {
	Write(context.Context, string, []byte) (string, error)
}
type StoreFunc func(context.Context, string, []byte) (string, error)

func (f StoreFunc) Write(ctx context.Context, key string, data []byte) (string, error) {
	return f(ctx, key, data)
}

// TraceRecorder is immutable while calls execute; its store and custom redactor
// must be concurrency-safe. Redaction occurs before the store receives data.
type TraceRecorder struct {
	Store        SnapshotStore
	ExtraSecrets []string
	Redactor     func(Packet) (Packet, error)
}

type LocalTraceStore struct{ Root string }

// Write atomically creates a private local snapshot. Existing files are retained.
func (store LocalTraceStore) Write(ctx context.Context, key string, data []byte) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if !filepath.IsLocal(key) {
		return "", errors.New("trace key must stay within root")
	}
	for _, part := range strings.Split(filepath.ToSlash(key), "/") {
		if part == ".." {
			return "", errors.New("trace key must stay within root")
		}
	}
	root, err := filepath.Abs(store.Root)
	if err != nil {
		return "", err
	}
	if err = os.MkdirAll(root, 0700); err != nil {
		return "", err
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return "", err
	}
	parent := root
	for _, part := range strings.Split(filepath.ToSlash(filepath.Dir(key)), "/") {
		if part == "." {
			continue
		}
		parent = filepath.Join(parent, part)
		if err = os.Mkdir(parent, 0700); err != nil && !os.IsExist(err) {
			return "", err
		}
		info, e := os.Lstat(parent)
		if e != nil {
			return "", e
		}
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return "", errors.New("trace parent must be a directory without symlinks")
		}
	}
	file, err := os.CreateTemp(parent, ".trace-")
	if err != nil {
		return "", err
	}
	temporary := file.Name()
	defer os.Remove(temporary)
	_, err = file.Write(data)
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return "", err
	}
	if err = ctx.Err(); err != nil {
		return "", err
	}
	destination := filepath.Join(parent, filepath.Base(key))
	if err = os.Link(temporary, destination); err != nil {
		return "", err
	}
	return destination, nil
}

var sensitiveKeys = map[string]bool{"authorization": true, "proxy_authorization": true, "api_key": true, "apikey": true, "x_api_key": true, "x_goog_api_key": true, "access_token": true, "refresh_token": true, "id_token": true, "password": true, "passwd": true, "secret": true, "client_secret": true, "cookie": true, "set_cookie": true, "private_key": true, "x_amz_security_token": true}

func sensitive(key string) bool {
	return sensitiveKeys[strings.ReplaceAll(strings.ToLower(key), "-", "_")]
}

var authPattern = regexp.MustCompile(`(?i)\b(Bearer|Basic)\s+[^\s"'<>;,]+`)
var labelPattern = regexp.MustCompile(`(?i)((?:["']?)(?:api[_-]?key|access_token|refresh_token|password|client_secret)(?:["']?)\s*[:=]\s*["']?)([^\s"'&,;}]+)`)
var urlAuthPattern = regexp.MustCompile(`(?i)(https?://)[^/@\s]+:[^/@\s]+@`)
var queryPattern = regexp.MustCompile(`(?i)([?&](?:key|token|api[_-]?key|access_token|refresh_token|x-amz-signature|x-amz-credential|x-amz-security-token|x-goog-signature|x-goog-credential)=)([^&#\s"']*)`)

func redact(value any, secrets []string) (any, error) {
	known := append([]string{}, secrets...)
	var discover func(any)
	discover = func(v any) {
		if p := object(v); p != nil {
			for key, item := range p {
				if s, ok := item.(string); sensitive(key) && ok {
					known = append(known, s)
					if containsAny(strings.ToLower(s), "bearer ", "basic ") {
						if _, tail, found := strings.Cut(s, " "); found {
							known = append(known, tail)
						}
					}
					if strings.Contains(strings.ToLower(key), "cookie") {
						for _, part := range strings.Split(s, ";") {
							if _, tail, ok := strings.Cut(part, "="); ok {
								known = append(known, strings.TrimSpace(tail))
							}
						}
					}
				}
				discover(item)
			}
		} else {
			for _, item := range array(v) {
				discover(item)
			}
		}
	}
	discover(value)
	variants := map[string]bool{}
	for _, secret := range known {
		if secret != "" {
			quoted, _ := json.Marshal(secret)
			for _, v := range []string{secret, url.QueryEscape(secret), url.PathEscape(secret), strings.ReplaceAll(url.QueryEscape(secret), "+", "%20"), string(quoted[1 : len(quoted)-1]), base64.StdEncoding.EncodeToString([]byte(secret))} {
				variants[v] = true
			}
		}
	}
	ordered := []string{}
	for s := range variants {
		ordered = append(ordered, s)
	}
	sort.Slice(ordered, func(i, j int) bool { return len(ordered[i]) > len(ordered[j]) })
	text := func(s string) string {
		for _, secret := range ordered {
			s = strings.ReplaceAll(s, secret, "[REDACTED]")
		}
		s = authPattern.ReplaceAllString(s, "${1} [REDACTED]")
		s = labelPattern.ReplaceAllString(s, "${1}[REDACTED]")
		s = queryPattern.ReplaceAllString(s, "${1}[REDACTED]")
		return urlAuthPattern.ReplaceAllString(s, "${1}[REDACTED]@")
	}
	var walk func(any) (any, error)
	walk = func(v any) (any, error) {
		if p := object(v); p != nil {
			out := Packet{}
			for key, item := range p {
				if sensitive(key) {
					out[text(key)] = "[REDACTED]"
				} else if key == "raw_body_base64" && item != nil {
					raw, err := base64.StdEncoding.DecodeString(str(item))
					if err != nil {
						return nil, err
					}
					out[key] = base64.StdEncoding.EncodeToString([]byte(text(string(raw))))
				} else {
					next, err := walk(item)
					if err != nil {
						return nil, err
					}
					out[text(key)] = next
				}
			}
			return out, nil
		}
		if a := array(v); a != nil {
			out := []any{}
			for _, item := range a {
				next, err := walk(item)
				if err != nil {
					return nil, err
				}
				out = append(out, next)
			}
			return out, nil
		}
		if s, ok := v.(string); ok {
			return text(s), nil
		}
		return v, nil
	}
	return walk(value)
}

func (recorder *TraceRecorder) record(ctx context.Context, snapshot Packet, secrets []string) (Packet, error) {
	if recorder == nil || recorder.Store == nil {
		return nil, errors.New("trace recorder requires a store")
	}
	ids := object(snapshot["ids"])
	job := textValue(first(ids["job_id"], ids["request_id"]))
	turn := textValue(first(ids["turn_id"], ids["request_id"]))
	key := "jobs/" + digest([]byte(job)) + "/turns/" + digest([]byte(turn)) + "/" + str(ids["invocation_id"]) + ".json"
	secrets = append(append([]string{}, secrets...), recorder.ExtraSecrets...)
	// Canonicalize before custom callbacks, so no pointer aliases to caller data escape.
	v, err := clone(snapshot)
	if err != nil {
		return nil, err
	}
	safe, err := redact(v, secrets)
	if err != nil {
		return nil, err
	}
	if recorder.Redactor != nil {
		safe, err = recorder.Redactor(object(safe))
		if err != nil {
			return nil, err
		}
		safe, err = redact(safe, secrets)
		if err != nil {
			return nil, err
		}
	}
	data, err := json.Marshal(safe)
	if err != nil {
		return nil, err
	}
	uri, err := recorder.Store.Write(ctx, key, data)
	if err != nil {
		return nil, err
	}
	return Packet{"status": "recorded", "uri": uri, "sha256": digest(data)}, nil
}

func observationFailure(err error) Packet {
	status := "failed"
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		status = "interrupted"
	}
	return Packet{"status": status, "error_type": fmt.Sprintf("%T", err)}
}

func observe(ctx context.Context, result, request, nativeRequest, nativeResponse Packet, id, key string, options Options) {
	start := time.Now()
	receipt := Packet{"invocation_id": id, "metrics": Packet{"status": "disabled"}, "trace": Packet{"status": "disabled"}}
	// A cancelled model call can still persist its final snapshot. The bounded
	// observation context is separate and callbacks must cooperate with its deadline.
	duration := options.ObservationTimeout
	if duration <= 0 {
		duration = 5 * time.Second
	}
	observationCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), duration)
	defer cancel()
	if options.Metrics != nil {
		measured := Packet{"invocation_id": id}
		for _, k := range []string{"request_id", "job_id", "turn_id", "duration_ms", "cost_usd", "status"} {
			measured[k] = result[k]
		}
		route := object(result["resolved_route"])
		measured["resolved_route"] = Packet{"route_id": route["route_id"], "provider": route["provider"], "model": route["model"]}
		usage := object(result["usage"])
		measured["usage"] = Packet{"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]}
		measured["error"] = Packet{"code": object(result["error"])["code"]}
		v, err := clone(measured)
		if err == nil {
			v, err = redact(v, []string{key})
		}
		if err == nil {
			err = options.Metrics(observationCtx, object(v))
		}
		if err != nil {
			receipt["metrics"] = observationFailure(err)
		} else {
			receipt["metrics"] = Packet{"status": "recorded"}
		}
	}
	if options.Recorder != nil || options.Debug {
		recorder := options.Recorder
		var err error
		if recorder == nil {
			root := os.Getenv("THINKER_TRACE_DIR")
			if root == "" {
				var home string
				home, err = os.UserHomeDir()
				root = filepath.Join(home, ".thinker", "traces")
			}
			recorder = &TraceRecorder{Store: LocalTraceStore{Root: root}}
		}
		usage := copyObject(result["usage"])
		delete(usage, "provider_data")
		route := object(result["resolved_route"])
		snapshot := Packet{"schema_version": "thinker.trace.v1", "ids": Packet{"request_id": result["request_id"], "job_id": result["job_id"], "turn_id": result["turn_id"], "invocation_id": id}, "mode": "summary", "started_at": result["started_at"], "completed_at": result["completed_at"], "duration_ms": result["duration_ms"], "status": result["status"], "route": Packet{"route_id": route["route_id"], "provider": route["provider"], "model": route["model"]}, "usage": usage, "cost_usd": result["cost_usd"], "error_code": object(result["error"])["code"], "metrics": receipt["metrics"]}
		if options.Debug {
			snapshot["mode"] = "debug"
			snapshot["request"] = request
			snapshot["native_request"] = nativeRequest
			snapshot["native_response"] = nativeResponse
			snapshot["response"] = result
		}
		var trace Packet
		if err == nil {
			trace, err = recorder.record(observationCtx, snapshot, []string{key})
		}
		if err != nil {
			receipt["trace"] = observationFailure(err)
		} else {
			receipt["trace"] = trace
		}
	}
	receipt["duration_ms"] = elapsed(start)
	result["metadata"] = Packet{"observability": receipt}
}
