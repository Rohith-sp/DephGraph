module example.com/demo

go 1.21

require github.com/gin-gonic/gin v1.9.0

require (
	github.com/bytedance/sonic v1.8.0 // indirect
	golang.org/x/net v0.10.0
)

replace example.com/old => ../old
