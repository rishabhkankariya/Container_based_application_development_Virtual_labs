# ==============================================================================
# Active Directory GPO Certificate Deployment Script
# ==============================================================================
# INSTRUCTIONS FOR IT ADMINISTRATOR:
# 1. Copy 'cert.crt' and this script ('deploy_gpo.ps1') to the Active Directory Domain Controller.
# 2. Open PowerShell as Administrator on the Domain Controller.
# 3. Run: .\deploy_gpo.ps1
# ==============================================================================

Import-Module GroupPolicy
Import-Module ActiveDirectory

$GpoName = "VirtualLabs_SSL_Certificate_Trust"
$CertFileName = "cert.crt"
$CurrentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CertPath = Join-Path $CurrentDir $CertFileName

if (-not (Test-Path $CertPath)) {
    Write-Error "Error: '$CertFileName' not found in $CurrentDir. Please place the certificate file in the same directory as this script."
    exit 1
}

# 1. Check if GPO already exists
$Gpo = Get-GPO -Name $GpoName -ErrorAction SilentlyContinue
if ($Gpo) {
    Write-Host "Group Policy Object '$GpoName' already exists. Linking it..."
} else {
    Write-Host "Creating new Group Policy Object: '$GpoName'..."
    $Gpo = New-GPO -Name $GpoName -Comment "Automatically trust the local SSL Certificate for Container Virtual Labs"
}

# 2. Link GPO to the root of the Domain (makes it active for all computers)
$DomainDN = (Get-ADDomain).DistinguishedName
Write-Host "Linking GPO to domain: $DomainDN..."
$GPLink = New-GPLink -Name $GpoName -Target $DomainDN -ErrorAction SilentlyContinue

# 3. Import the Certificate into the GPO's Trusted Root CA Store
# In Windows AD, GPO certificates are stored in Active Directory LDAP directory services under the public key policies container.
Write-Host "Registering '$CertFileName' into the GPO system store..."
try {
    # Define LDAP Path for the policy certificates
    $domainController = (Get-ADDomainController).HostName
    $certStoreLDAPPath = "LDAP://$domainController/CN=System,CN=Policies,CN=System,$DomainDN"
    
    # Run certutil to publish the certificate to the domain's enterprise root store
    certutil.exe -dspublish -f "$CertPath" Root
    
    Write-Host "--------------------------------------------------------"
    Write-Host "SUCCESS: Certificate has been published to the domain Enterprise Root store!"
    Write-Host "It will propagate to all domain computers automatically on the next Group Policy refresh."
    Write-Host "To force updates on client computers, clients can run: gpupdate /force"
    Write-Host "--------------------------------------------------------"
}
catch {
    Write-Error "An error occurred publishing the certificate: $_"
}
