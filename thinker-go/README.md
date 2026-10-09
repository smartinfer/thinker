# Thinker Go Library

Native Go 1.21+ library for stateless ThinkerQL 0.4 model calls. No Python runtime,
Thinker HTTP service, or external Go module dependencies are required.

```go
result := thinker.ModelCall(ctx, packet, route, thinker.Options{})
```

Call from host-owned goroutines for concurrency. The host selects the model,
supplies history, executes tools, and owns validation/recovery. Output constraints
are optional; generated text and raw tool arguments are preserved. Metrics and
trace storage are off by default.

Read the [Go user manual](../docs/GO_USER_MANUAL.md) for setup, full examples,
options, observability, Python differences, and the Dolphin migration boundary.

```sh
go test -race ./...
go vet ./...
```
