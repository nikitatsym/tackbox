//lint:file-ignore U1000 file-wide suppression is not supported
package directives

//lint:ignore U1000 declaration suppression is not supported
func lintIgnored() {} // want `ERC010: func lintIgnored is unused \(U1000\)`

// no-report: unused declarations must be removed instead
func markerIgnored() {} // want `ERC010: func markerIgnored is unused \(U1000\)`
