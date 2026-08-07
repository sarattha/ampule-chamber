// Command k6 builds the upstream k6 CLI with patched Go modules.
package main

import "go.k6.io/k6/v2/cmd"

func main() {
	cmd.Execute()
}
