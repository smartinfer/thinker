// Package thinker provides stateless native model calls using ThinkerQL 0.4.
// The host owns model selection, conversation state, tools and recovery.
package thinker

import (
	"bytes"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"reflect"
	"strings"
	"unicode/utf8"
)

const Version = "0.4"

// Packet is JSON-shaped data. Generated content and tool arguments stay opaque.
// Do not mutate a packet concurrently with a call that is copying it.
type Packet = map[string]any

//go:embed contract/*.json
var contracts embed.FS

// These private schemas are immutable after initialization; calls only read them.
var envelopeSchemas = func() map[string]Packet {
	out := map[string]Packet{}
	for _, kind := range []string{"request", "response"} {
		data, err := PacketSchema(kind)
		if err != nil {
			panic("missing embedded packet schema")
		}
		value, err := decodeJSON(data)
		if err != nil {
			panic("invalid embedded packet schema")
		}
		out[kind] = object(value)
	}
	return out
}()

// PacketSchema returns an independent copy of the embedded envelope schema.
func PacketSchema(kind string) ([]byte, error) {
	if kind != "request" && kind != "response" {
		return nil, errors.New("unknown packet kind")
	}
	return contracts.ReadFile("contract/" + kind + "_v0_4.schema.json")
}

func object(value any) Packet {
	if p, ok := value.(map[string]any); ok {
		return p
	}
	return nil
}
func array(value any) []any {
	if p, ok := value.([]any); ok {
		return p
	}
	return nil
}
func str(value any) string          { s, _ := value.(string); return s }
func has(p Packet, key string) bool { _, ok := p[key]; return ok }

func truthy(v any) bool {
	if v == nil || v == false || v == "" {
		return false
	}
	if p := object(v); p != nil {
		return len(p) > 0
	}
	if a := array(v); a != nil {
		return len(a) > 0
	}
	if n, ok := number(v); ok {
		return n.Sign() != 0
	}
	return true
}
func fallback(value any, otherwise any) any {
	if value == nil {
		return otherwise
	}
	return value
}
func stringOrJSON(value any) string {
	if s, ok := value.(string); ok {
		return s
	}
	var encoded bytes.Buffer
	encoder := json.NewEncoder(&encoded)
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(value) // Input already passed JSON envelope checks.
	// Match Python's separator spacing when serializing structured tool results.
	// Already-string arguments/results above are returned byte-for-byte unchanged.
	var result strings.Builder
	quoted, escaped := false, false
	for _, char := range strings.TrimSuffix(encoded.String(), "\n") {
		result.WriteRune(char)
		if escaped {
			escaped = false
			continue
		}
		if quoted && char == '\\' {
			escaped = true
			continue
		}
		if char == '"' {
			quoted = !quoted
		}
		if !quoted && (char == ':' || char == ',') {
			result.WriteByte(' ')
		}
	}
	return result.String()
}
func copyObject(value any) Packet {
	p := Packet{}
	for k, v := range object(value) {
		p[k] = v
	}
	return p
}
func number(value any) (*big.Rat, bool) {
	n, ok := value.(json.Number)
	if !ok {
		return nil, false
	}
	r, ok := new(big.Rat).SetString(string(n))
	return r, ok
}

// decodeJSON preserves numeric precision and rejects duplicate envelope keys.
// It never parses a generated string as JSON.
func decodeJSON(data []byte) (any, error) {
	if !utf8.Valid(data) {
		return nil, errors.New("invalid UTF-8 in JSON envelope")
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	var read func(int) (any, error)
	read = func(depth int) (any, error) {
		if depth > 256 {
			return nil, errors.New("JSON nesting exceeds 256 levels")
		}
		t, err := d.Token()
		if err != nil {
			return nil, err
		}
		switch t {
		case json.Delim('{'):
			p := Packet{}
			for d.More() {
				key, err := d.Token()
				if err != nil {
					return nil, err
				}
				k, ok := key.(string)
				if !ok || has(p, k) {
					return nil, errors.New("invalid or duplicate JSON key")
				}
				p[k], err = read(depth + 1)
				if err != nil {
					return nil, err
				}
			}
			_, err = d.Token()
			return p, err
		case json.Delim('['):
			a := []any{}
			for d.More() {
				v, err := read(depth + 1)
				if err != nil {
					return nil, err
				}
				a = append(a, v)
			}
			_, err = d.Token()
			return a, err
		default:
			return t, nil
		}
	}
	v, err := read(0)
	if err != nil {
		return nil, err
	}
	if _, err = d.Token(); err != io.EOF {
		return nil, errors.New("trailing JSON data")
	}
	return v, nil
}

func clone(value any) (any, error) {
	data, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	return decodeJSON(data)
}

// ParseRequest checks only the ThinkerQL envelope and returns a detached packet.
func ParseRequest(packet Packet) (Packet, error) { return parsePacket(packet, "request") }

// ParseResponse checks only the ThinkerQL envelope, not generated content.
func ParseResponse(packet Packet) (Packet, error) { return parsePacket(packet, "response") }

func parsePacket(packet Packet, kind string) (Packet, error) {
	v, err := clone(packet)
	if err == nil {
		root := envelopeSchemas[kind]
		if root != nil && matches(v, root, root) {
			return object(v), nil
		}
	}
	return nil, fmt.Errorf("invalid ThinkerQL %s envelope", kind)
}

// matches implements only the keywords used by our embedded envelope schemas.
// It is not a general JSON Schema API and never visits caller output schemas.
func matches(v any, s, root Packet) bool {
	if ref := str(s["$ref"]); ref != "" {
		if !strings.HasPrefix(ref, "#/$defs/") {
			return false
		}
		target := object(object(root["$defs"])[strings.TrimPrefix(ref, "#/$defs/")])
		return target != nil && matches(v, target, root)
	}
	if c, ok := s["const"]; ok && !reflect.DeepEqual(v, c) {
		return false
	}
	if choices, ok := s["enum"]; ok {
		found := false
		for _, c := range array(choices) {
			if reflect.DeepEqual(v, c) {
				found = true
			}
		}
		if !found {
			return false
		}
	}
	if types, ok := s["type"]; ok {
		list := array(types)
		if list == nil {
			list = []any{types}
		}
		found := false
		for _, t := range list {
			switch t {
			case "object":
				found = found || object(v) != nil
			case "array":
				found = found || array(v) != nil
			case "string":
				_, ok := v.(string)
				found = found || ok
			case "boolean":
				_, ok := v.(bool)
				found = found || ok
			case "null":
				found = found || v == nil
			case "number":
				_, ok := number(v)
				found = found || ok
			case "integer":
				n, ok := number(v)
				found = found || (ok && n.IsInt())
			}
		}
		if !found {
			return false
		}
	}
	if p := object(v); p != nil {
		for _, key := range array(s["required"]) {
			if !has(p, str(key)) {
				return false
			}
		}
		for key, deps := range object(s["dependentRequired"]) {
			if has(p, key) {
				for _, dep := range array(deps) {
					if !has(p, str(dep)) {
						return false
					}
				}
			}
		}
		props := object(s["properties"])
		for key, item := range p {
			if rule, ok := props[key]; ok {
				if !matches(item, object(rule), root) {
					return false
				}
			} else if s["additionalProperties"] == false {
				return false
			}
		}
	}
	if a := array(v); a != nil {
		if min, ok := number(s["minItems"]); ok && new(big.Rat).SetInt64(int64(len(a))).Cmp(min) < 0 {
			return false
		}
		if items, ok := s["items"]; ok {
			for _, item := range a {
				if !matches(item, object(items), root) {
					return false
				}
			}
		}
	}
	if text, ok := v.(string); ok {
		if min, ok := number(s["minLength"]); ok && new(big.Rat).SetInt64(int64(utf8.RuneCountInString(text))).Cmp(min) < 0 {
			return false
		}
	}
	if n, ok := number(v); ok {
		if min, ok := number(s["minimum"]); ok && n.Cmp(min) < 0 {
			return false
		}
		if max, ok := number(s["maximum"]); ok && n.Cmp(max) > 0 {
			return false
		}
	}
	for _, sub := range array(s["allOf"]) {
		if !matches(v, object(sub), root) {
			return false
		}
	}
	for _, key := range []string{"anyOf", "oneOf"} {
		if choices, ok := s[key]; ok {
			count := 0
			for _, sub := range array(choices) {
				if matches(v, object(sub), root) {
					count++
				}
			}
			if count == 0 || (key == "oneOf" && count != 1) {
				return false
			}
		}
	}
	if sub, ok := s["not"]; ok && matches(v, object(sub), root) {
		return false
	}
	if sub, ok := s["if"]; ok {
		branch := "else"
		if matches(v, object(sub), root) {
			branch = "then"
		}
		if rule, ok := s[branch]; ok && !matches(v, object(rule), root) {
			return false
		}
	}
	return true
}
