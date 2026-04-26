// Contract Intelligence POC — single-file Bicep deployment.
//
// Deploys:
//   - Azure Database for PostgreSQL Flexible Server (B1ms, PG16, vector extension)
//   - Linux VM (Ubuntu 24.04 LTS) with public IP + DNS label
//   - NSG allowing 22 (SSH) and 443 (HTTPS via Caddy)
//   - Cloud-init that installs Docker/uv/Caddy, clones the repo, and starts services
//
// Self-contained: no remote modules, no registry pulls.

@description('Deployment region.')
param location string = resourceGroup().location

@description('Short prefix for resource names. Lowercase, 3-12 chars.')
@minLength(3)
@maxLength(12)
param namePrefix string = 'cipoc'

@description('VM admin username.')
param adminUsername string = 'azureuser'

@description('SSH public key for the VM admin user. Paste the contents of ~/.ssh/id_rsa.pub.')
param sshPublicKey string

@description('PostgreSQL admin login.')
param pgAdminUser string = 'cipocadmin'

@description('PostgreSQL admin password. Min 8 chars, mix of upper/lower/digit.')
@secure()
@minLength(12)
param pgAdminPassword string

@description('Bearer token clients must send to reach the MCP server (via Caddy).')
@secure()
@minLength(16)
param mcpBearerToken string

@description('Anthropic API key (sk-ant-...). Stored on the VM in /etc/contract-intel/env.')
@secure()
param anthropicApiKey string

@description('Voyage API key (pa-...).')
@secure()
param voyageApiKey string

@description('Git repository to clone on the VM.')
param repoUrl string = 'https://github.com/Ocularitas/automatic-garbanzo.git'

@description('Branch to check out.')
param repoBranch string = 'claude/review-claude-files-uaJD7'

@description('VM size. Standard_B2s is a sensible POC default.')
param vmSize string = 'Standard_B2s'

@description('PostgreSQL Flexible Server SKU.')
param pgSkuName string = 'Standard_B1ms'

// --- Derived names ----------------------------------------------------------

var uniq         = uniqueString(resourceGroup().id)
var pgServerName = toLower('${namePrefix}-pg-${uniq}')
var vmName       = '${namePrefix}-vm'
var dnsLabel     = toLower('${namePrefix}-${uniq}')
var fqdn         = '${dnsLabel}.${location}.cloudapp.azure.com'
var nicName      = '${vmName}-nic'
var pipName      = '${vmName}-pip'
var nsgName      = '${vmName}-nsg'
var vnetName     = '${namePrefix}-vnet'

// --- Networking -------------------------------------------------------------

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: nsgName
  location: location
  properties: {
    securityRules: [
      {
        name: 'AllowSSH'
        properties: {
          priority: 1000
          access: 'Allow'
          direction: 'Inbound'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      {
        name: 'AllowHTTPS'
        properties: {
          priority: 1010
          access: 'Allow'
          direction: 'Inbound'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
      {
        name: 'AllowHTTP'
        properties: {
          // Needed only briefly so Let's Encrypt's HTTP-01 challenge works on first run.
          priority: 1020
          access: 'Allow'
          direction: 'Inbound'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '80'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [ '10.20.0.0/16' ]
    }
    subnets: [
      {
        name: 'default'
        properties: {
          addressPrefix: '10.20.1.0/24'
          networkSecurityGroup: { id: nsg.id }
        }
      }
    ]
  }
}

resource pip 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: pipName
  location: location
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: {
      domainNameLabel: dnsLabel
    }
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-01-01' = {
  name: nicName
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: '${vnet.id}/subnets/default' }
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: { id: pip.id }
        }
      }
    ]
  }
}

// --- PostgreSQL Flexible Server --------------------------------------------

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: pgServerName
  location: location
  sku: {
    name: pgSkuName
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: pgAdminUser
    administratorLoginPassword: pgAdminPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

// Allow inbound from any Azure-internal source (the VM lives there).
resource fwAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: pg
  name: 'AllowAllAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// Enable pgvector. Without this, CREATE EXTENSION vector fails.
resource pgExtensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview' = {
  parent: pg
  name: 'azure.extensions'
  properties: {
    value: 'VECTOR'
    source: 'user-override'
  }
}

resource pgDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: pg
  name: 'contract_intel'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
  dependsOn: [ pgExtensions ]
}

// --- VM ---------------------------------------------------------------------

// cloud-init.yaml is loaded as text and templated with deployment-specific values.
var cloudInitRaw = loadTextContent('cloud-init.yaml')
var cloudInit = replace(replace(replace(replace(replace(replace(replace(replace(
  cloudInitRaw,
  '__REPO_URL__',         repoUrl),
  '__REPO_BRANCH__',      repoBranch),
  '__PG_HOST__',          pg.properties.fullyQualifiedDomainName),
  '__PG_USER__',          '${pgAdminUser}'),
  '__PG_PASSWORD__',      pgAdminPassword),
  '__BEARER_TOKEN__',     mcpBearerToken),
  '__ANTHROPIC_KEY__',    anthropicApiKey),
  '__VOYAGE_KEY__',       voyageApiKey)

resource vm 'Microsoft.Compute/virtualMachines@2024-03-01' = {
  name: vmName
  location: location
  properties: {
    hardwareProfile: { vmSize: vmSize }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer:     'ubuntu-24_04-lts'
        sku:       'server'
        version:   'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
      }
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      customData: base64(replace(cloudInit, '__CADDY_DOMAIN__', fqdn))
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    networkProfile: {
      networkInterfaces: [ { id: nic.id } ]
    }
  }
}

// --- Outputs ---------------------------------------------------------------

output mcpUrl       string = 'https://${fqdn}/mcp/'
output sshCommand   string = 'ssh ${adminUsername}@${fqdn}'
output pgFqdn       string = pg.properties.fullyQualifiedDomainName
output pgDatabase   string = 'contract_intel'
output databaseUrl  string = 'postgresql+psycopg://${pgAdminUser}:<password>@${pg.properties.fullyQualifiedDomainName}:5432/contract_intel?sslmode=require'
