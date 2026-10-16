# Compare one flight's price: our supplier API vs the gujjutours.com website.
#
# Shows, from the raw responses:
#   1. Our API returns the fare already in INR.
#   2. Our ROE API returns the INR exchange rates.
#   3. The website page carries the same ROE list and the search's conversationId.
#   4. The website's fare API returns AED base amounts (labelled "USD"/"INR").
#   5. Website price = AED amount x sellingROE (or / buyingROE), and how it
#      compares with ours.
#
# Run from the repo root in the VS Code terminal:
#   powershell -ExecutionPolicy Bypass -File scripts\compare_flight_prices.ps1
# Optional: -Flights "AI1117+AI4209 / AI912+AI493" -Airline AI
param(
    [string]$Origin = "AMD",
    [string]$Destination = "DXB",
    [string]$Depart = "20-10-2026",
    [string]$Return = "26-10-2026",
    [int]$Adults = 2,
    [string]$Airline = "FZ",
    [string]$Flights = "FZ438 / FZ437"
)
$ErrorActionPreference = "Stop"

# --- token from .env (never printed) -----------------------------------------
$envLine = Get-Content .env | Where-Object { $_ -match '^BOOKING_TOKEN=' } | Select-Object -First 1
if (-not $envLine) { throw "BOOKING_TOKEN not found in .env" }
$token = ($envLine -split '=', 2)[1].Trim().Trim('"')
$tenant = "DB1EC027-BDEC-3EA4-EDE7-3A1BE86F63F6"
$api = "https://stagingapi.gujjutours.com"

function Key($it) {
    ($it.originDestinationOptions | ForEach-Object {
        ($_.flightSegments | ForEach-Object { "$($_.marketingAirlineCode)$($_.flightNumber)" }) -join "+"
    }) -join " / "
}

Write-Host "`n=== 1. OUR flight API ===" -ForegroundColor Cyan
$body = @{
    OriginDestinationInformations = @(
        @{ DepartureDateTime = $Depart; OriginLocationCode = $Origin; DestinationLocationCode = $Destination },
        @{ DepartureDateTime = $Return; OriginLocationCode = $Destination; DestinationLocationCode = $Origin }
    )
    TravelPreferences = @{ MaxStopsQuantity = "1"; CabinPreference = "Y"; AirTripType = "return" }
    PricingSourceType = "all"
    PassengerTypeQuantities = @(@{ Code = "ADT"; Quantity = $Adults })
    childAge = ""; infantAge = ""; Target = "test"; agentID = 0; rateCategoryId = 0
    supplierTime = "6"; supplierId = 0; suppliers = @(); isMobile = 0; AirlineName = ""
} | ConvertTo-Json -Depth 6
$headers = @{
    Authorization = "Bearer $token"; "X-API-Key" = $token; "X-Site-Type" = "B2B"
    "X-Tenant-Id" = $tenant; "X-Time-Zone" = "Arabian Standard Time"; accept = "text/plain"
}
Write-Host "POST $api/api/Flight/search"
$ours = Invoke-RestMethod -Method Post -Uri "$api/api/Flight/search" -Headers $headers -ContentType "application/json" -Body $body
$ourRows = $ours.data.pricedItineraries | Where-Object { (Key $_) -eq $Flights }
foreach ($r in $ourRows) {
    $t = $r.airItineraryPricingInfo.itinTotalFare.totalFare
    Write-Host ("  {0,-14} totalFare = {1} {2}" -f ([string]$r.providerName), $t.amount, $t.currencyCode)
}

Write-Host "`n=== 2. OUR ROE API ===" -ForegroundColor Cyan
Write-Host "GET $api/api/Currency/ROE/INR"
$roe = (Invoke-RestMethod -Uri "$api/api/Currency/ROE/INR" -Headers @{ Authorization = "Bearer $token" }).result
Write-Host ("  INR buyingROE = {0}   sellingROE = {1}" -f $roe.buyingROE, $roe.sellingROE)

Write-Host "`n=== 3. WEBSITE page (conversationId + its ROE list) ===" -ForegroundColor Cyan
$page = "https://www.gujjutours.com/en/flights/listing-flights?pick_up_date=$Depart&drop_off_date=$Return" +
        "&pick_up_code=$Origin&drop_off_code=$Destination&air_trip_type=Return&adults=$Adults&children=0&infant=0" +
        "&airlines=$Airline&is_direct_flights=1&cabin_type=Y&sort_by=cheapest"
Write-Host "GET $page"
$html = (Invoke-WebRequest -UseBasicParsing -Uri $page -UserAgent "Mozilla/5.0").Content
$cid = [regex]::Match($html, 'conversationId\\":\\"([0-9a-f-]+)').Groups[1].Value
$m = [regex]::Match($html, 'currencyCode\\":\\"INR\\",\\"buyingROE\\":([0-9.]+),\\"sellingROE\\":([0-9.]+)')
Write-Host "  conversationId = $cid"
Write-Host ("  website INR buyingROE = {0}   sellingROE = {1}" -f $m.Groups[1].Value, $m.Groups[2].Value)

Write-Host "`n=== 4. WEBSITE fare API ===" -ForegroundColor Cyan
Write-Host "POST https://www.gujjutours.com/api/flights/flight-async-list  {conversationId, language}"
do {
    $site = Invoke-RestMethod -Method Post -Uri "https://www.gujjutours.com/api/flights/flight-async-list" `
        -ContentType "application/json" -Body (@{ conversationId = $cid; language = "en" } | ConvertTo-Json)
    if (-not $site.isCompleted) { Start-Sleep -Seconds 2 }
} until ($site.isCompleted)
Write-Host ("  searchCurrency requested by the website = {0}" -f $site.data.flightSearchRequestData.searchCurrency)
$siteRows = $site.data.pricedItineraries | Where-Object { (Key $_) -eq $Flights }
foreach ($r in $siteRows) {
    $t = $r.airItineraryPricingInfo.itinTotalFare.totalFare
    Write-Host ("  {0,-14} totalFare = {1} labelled {2}  (AED base amount)" -f ([string]$r.providerName), $t.amount, $t.currencyCode)
}

Write-Host "`n=== 5. Calculation (same ROE on both sides) ===" -ForegroundColor Cyan
$buy = [double]$roe.buyingROE; $sell = [double]$roe.sellingROE
foreach ($s in $siteRows) {
    $prov = [string]$s.providerName
    $amt = [double]$s.airItineraryPricingInfo.itinTotalFare.totalFare.amount
    $label = $s.airItineraryPricingInfo.itinTotalFare.totalFare.currencyCode
    if ($label -eq "INR") { $siteInr = $amt / $buy; $how = "$amt / buyingROE $buy" }
    else { $siteInr = $amt * $sell; $how = "$amt x sellingROE $sell" }
    $o = $ourRows | Where-Object { [string]$_.providerName -eq $prov } | Select-Object -First 1
    if (-not $o) { Write-Host "  $prov : not in our results right now"; continue }
    $ourInr = [double]$o.airItineraryPricingInfo.itinTotalFare.totalFare.amount
    if ($label -eq "INR") { $ourAed = $ourInr * $buy } else { $ourAed = $ourInr / $sell }
    Write-Host ("  [{0}]" -f ($(if ($prov) { $prov } else { "public" })))
    Write-Host ("    website : {0} = INR {1:N2}" -f $how, $siteInr)
    Write-Host ("    ours    : INR {0:N2} (received in INR)  ->  AED base {1:N4}" -f $ourInr, $ourAed)
    Write-Host ("    website AED / our AED = {0:N5}    website INR / our INR = {1:N5}" -f ($amt / $ourAed), ($siteInr / $ourInr))
}
