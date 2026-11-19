# Usage: .\scripts\rag_demo.ps1 [-ApiKey ADMIN_KEY]
param([string]$Base = "http://127.0.0.1:8000", [string]$ApiKey = "")
$headers = @{}
if ($ApiKey) { $headers["Authorization"] = "Bearer $ApiKey" }

$doc = @{
    doc_id = "refund-policy"
    collection = "finance"
    text = "Refund policy: customers can request a refund within 30 days of purchase. Refunds are paid back to the original payment method within 5 business days."
} | ConvertTo-Json
Invoke-RestMethod "$Base/api/v1/rag/ingest" -Method Post -ContentType "application/json" -Headers $headers -Body $doc

$q = @{ question = "How many days do customers have to ask for a refund?"; collection = "finance" } | ConvertTo-Json
Invoke-RestMethod "$Base/api/v1/rag/query" -Method Post -ContentType "application/json" -Headers $headers -Body $q | ConvertTo-Json -Depth 5
