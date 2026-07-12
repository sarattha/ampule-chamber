// Command kubectl builds the upstream Kubernetes CLI with patched Go modules.
package main

import (
	"os"

	"k8s.io/component-base/cli"
	"k8s.io/component-base/logs"
	"k8s.io/kubectl/pkg/cmd"
	"k8s.io/kubectl/pkg/cmd/util"

	// Initialize the standard client authentication plugins.
	_ "k8s.io/client-go/plugin/pkg/client/auth"
)

func main() {
	logs.GlogSetter(cmd.GetLogVerbosity(os.Args)) //nolint:errcheck
	command := cmd.NewDefaultKubectlCommand()
	if err := cli.RunNoErrOutput(command); err != nil {
		util.CheckErr(err)
	}
}
