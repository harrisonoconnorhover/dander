# Azure stage-zero bootstrap

This one-time root creates the resource group, firewall-restricted private/versioned Azure Storage state container,
Basic ACR with administrator credentials disabled, and one user-assigned runtime identity. It uses
the signed-in Azure CLI principal and deliberately disables automatic resource-provider
registration.

The Dander CLI first plans this root against secured local state in an operator directory outside
the repository. After an explicitly approved apply, it migrates that state into the new Azure
Storage backend using Entra authentication and records only the non-secret backend coordinates.
Subsequent plans must match that record exactly.

Do not commit the saved plan, local state, backend metadata, or `.terraform/` directory. The
generated identity client id and reviewed exact operator IP are non-secret reproducibility inputs;
secret values and credentials are not outputs of this root.
