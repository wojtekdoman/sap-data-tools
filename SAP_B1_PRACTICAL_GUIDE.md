# SAP Business One — Praktyczny Przewodnik

> Skondensowana wiedza zebrana podczas pracy z konkretną instancją SAP B1.
> Zawiera dane połączeniowe, nazwy pól, UDF-y, mapowania, quirki i pitfalle.
> Używaj tego pliku jako referencji przy każdym nowym projekcie łączącym się z tym SAP-em.

---

## Spis treści

1. [Dane połączeniowe](#1-dane-połączeniowe)
2. [Autentykacja i sesje](#2-autentykacja-i-sesje)
3. [OData API — wzorce i składnia](#3-odata-api--wzorce-i-składnia)
4. [Tabele SAP SQL — referencja](#4-tabele-sap-sql--referencja)
5. [Mapowanie pól: Service Layer ↔ SQL](#5-mapowanie-pól-service-layer--sql)
6. [Enumy i typy danych](#6-enumy-i-typy-danych)
7. [Business Partners (OCRD)](#7-business-partners-ocrd)
8. [Items / Produkty (OITM)](#8-items--produkty-oitm)
9. [Faktury i dokumenty](#9-faktury-i-dokumenty)
10. [Zamówienia sprzedaży i zakupu](#10-zamówienia-sprzedaży-i-zakupu)
11. [VAT / Kody podatkowe](#11-vat--kody-podatkowe)
12. [Waluty](#12-waluty)
13. [User-Defined Fields (UDF)](#13-user-defined-fields-udf)
14. [User-Defined Tables (UDT)](#14-user-defined-tables-udt)
15. [Obsługa błędów](#15-obsługa-błędów)
16. [Krytyczne pułapki](#16-krytyczne-pułapki)
17. [Endpointy Service Layer — referencja](#17-endpointy-service-layer--referencja)
18. [Połączenie bezpośrednie z SQL Server](#18-połączenie-bezpośrednie-z-sql-server)

---

## 1. Dane połączeniowe

### Service Layer (OData REST API)

```
URL:          https://212.91.25.118:50000/b1s/v1
Alt URL:      https://217.17.34.62:50000/b1s/v1   (ten sam serwer, ten sam cert "SBOSQL")
CompanyDB:    SBO_DOMSON_PL
UserName:     manager
Password:     11011976
SSL:          Self-signed cert — wymagane verify=false
```

### SQL Server (bezpośredni dostęp do bazy)

```
Host:         SBOSQL (ten sam serwer co Service Layer)
Database:     SBO_DOMSON_PL
Engine:       Microsoft SQL Server
```

Dostęp do SQL realizowany jest przez sync agenta (Python, `C:\SAPSync\sync_agent.py` na serwerze SAP) który replikuje dane do lokalnego PostgreSQL. Można też łączyć się bezpośrednio przez pyodbc/pymssql jeśli jest tunel SSH.

### Branches (BPL — Business Places)

| BPL_ID | Nazwa | Kraj |
|--------|-------|------|
| 3 | Domson Poland | PL |
| (inne) | London, Midlands | GB |

`BPL_IDAssignedToInvoice` — obowiązkowe pole na każdym dokumencie (faktura, zamówienie).

---

## 2. Autentykacja i sesje

### Login

```
POST /Login
Content-Type: application/json

{
    "CompanyDB": "SBO_DOMSON_PL",
    "UserName": "manager",
    "Password": "11011976"
}
```

Odpowiedź ustawia cookies: `B1SESSION` + `ROUTEID`. Każdy kolejny request musi je zawierać.

### Cykl życia sesji

| Parametr | Wartość |
|----------|---------|
| Timeout | 30 minut bezczynności |
| Wykrycie wygaśnięcia | HTTP 401 Unauthorized |
| Strategia | Auto-retry: re-login → powtórz request (max 2 próby) |
| Proaktywne odświeżanie | `GET /$metadata` co ~25 minut |

### Logout

```
POST /Logout
```

Nieobowiązkowe — sesja i tak wygaśnie po 30 min.

### Zabezpieczenie przed race condition

Używaj `asyncio.Lock()` aby zapobiec równoczesnym loginom z wielu coroutines:

```python
self._session_lock = asyncio.Lock()

async def _ensure_session(self):
    async with self._session_lock:
        if self._is_session_valid():
            return
        await self._login()
```

### Przechowywanie sesji w Redis (multi-instance)

Dla wielu instancji aplikacji — cookies w Redis z TTL:
- Klucz: `{app}:sap_session:{username}:{company_db}`
- TTL: czas do wygaśnięcia minus bufor (np. 25 min)

---

## 3. OData API — wzorce i składnia

### Podstawowa składnia

```
GET /Items?$select=ItemCode,ItemName,BarCode
          &$filter=ItemCode eq 'G001'
          &$top=20
          &$skip=0
          &$orderby=ItemName asc
```

### Funkcje filtrowania

```
# Zawiera (częściowe dopasowanie)
$filter=contains(ItemName, 'Widget')

# Zaczyna się od
$filter=startswith(CardCode, 'C1')

# Wiele warunków
$filter=DocDate ge '2024-01-01' and DocDate le '2024-12-31' and Cancelled eq 'tNO'

# WAŻNE: SAP używa stringowych enumów!
$filter=Cancelled eq 'tNO'              # NIE: false
$filter=DocumentStatus eq 'bost_Open'   # NIE: 0 czy true
$filter=CardType eq 'cCustomer'         # NIE: 'C'
```

### Escape'owanie w filtrach

Apostrofy w wartościach: podwójny apostrof `''`:
```
$filter=contains(ItemName, 'O''Brien')
```

### Paginacja

Domyślny rozmiar strony: **20 rekordów**.

```python
skip = 0
while True:
    response = await client.get(f"/Items?$top=20&$skip={skip}")
    data = response.json()
    items = data.get("value", [])
    if not items:
        break
    # przetwarzaj...
    if "odata.nextLink" not in data:
        break
    skip += 20
    await asyncio.sleep(0.2)  # 200ms między stronami — chroni przed 500
```

Większa strona: header `Prefer: odata.maxpagesize=500`.

### POST (tworzenie)

```python
response = await client.post("/Items", json=payload)
# 201 Created — pełny obiekt w body
```

### PATCH (aktualizacja)

```python
response = await client.patch(f"/Items('{item_code}')", json={"BarCode": "590..."})
# 204 No Content — BEZ response body!
```

### DELETE

```python
response = await client.delete(f"/Drafts({doc_entry})")
# 204 No Content
```

### Format odpowiedzi błędu

```json
{
    "error": {
        "code": -10002,
        "message": {
            "lang": "en-us",
            "value": "Opis błędu"
        }
    }
}
```

### Czyszczenie metadanych OData

Przed użyciem pobranych danych w PATCH — usuń pola systemowe:
- `odata.metadata`
- `odata.etag`
- `odata.type`

---

## 4. Tabele SAP SQL — referencja

### Tabele główne

| Tabela SQL | Service Layer Endpoint | Zawartość |
|------------|----------------------|-----------|
| OITM | `/Items` | Produkty (master data) |
| OITW | (sub-collection Items) | Stany magazynowe per warehouse |
| ITM1 | (sub-collection Items) | Cenniki per price list |
| OCRD | `/BusinessPartners` | Kontrahenci (klienci/dostawcy) |
| CRD1 | (sub-collection BP) | Adresy kontrahentów |
| CRD7 | (sub-collection BP) | Dane podatkowe kontrahentów |
| OCRB | (sub-collection BP) | Konta bankowe kontrahentów |
| OSCN | `/AlternateCatNum` | Alternatywne numery katalogowe |
| OINV | `/Invoices` | Faktury sprzedaży (nagłówki) |
| INV1 | (DocumentLines) | Linie faktur sprzedaży |
| ORIN | `/CreditNotes` | Korekty sprzedaży (nagłówki) |
| RIN1 | (DocumentLines) | Linie korekt sprzedaży |
| OPCH | `/PurchaseInvoices` | Faktury zakupu (nagłówki) |
| PCH1 | (DocumentLines) | Linie faktur zakupu |
| ORDR | `/Orders` | Zamówienia sprzedaży (nagłówki) |
| RDR1 | (DocumentLines) | Linie zamówień sprzedaży |
| OPOR | `/PurchaseOrders` | Zamówienia zakupu (nagłówki) |
| POR1 | (DocumentLines) | Linie zamówień zakupu |
| OWHS | `/Warehouses` | Magazyny |
| OMRC | `/Manufacturers` | Producenci |
| OITB | `/ItemGroups` | Grupy towarowe |
| OSLP | `/SalesPersons` | Handlowcy |
| OPLN | `/PriceLists` | Definicje cenników |
| SPP1 | `/SpecialPrices` | Ceny specjalne (per kontrahent) |
| OCTG | `/PaymentTermsTypes` | Warunki płatności |
| OHEM | `/EmployeesInfo` | Pracownicy |
| OBTD | — | Typy transakcji bankowych |

### Tabele UDT (User-Defined Tables)

| Tabela SQL | Zawartość |
|------------|-----------|
| @DRILO | Ładunki kierowców (Drive Loads) |
| @DRIMI | Pozycje kierowców (Drive Items) |
| @VEHLO | Ładunki pojazdów (Vehicle Loads) |
| @VEHMI | Pozycje pojazdów (Vehicle Items) |

---

## 5. Mapowanie pól: Service Layer ↔ SQL

### OCRD (Business Partners / Kontrahenci)

| Service Layer (OData) | SQL Column | Typ | Uwagi |
|----------------------|------------|-----|-------|
| CardCode | CardCode | varchar(15) | Klucz główny |
| CardName | CardName | varchar(100) | Nazwa firmy |
| CardType | CardType | char(1) | C=customer, S=supplier, L=lead |
| FederalTaxID | LicTradNum | varchar(32) | NIP — może mieć prefix "PL" |
| EmailAddress | E_Mail | varchar(100) | |
| Phone1 | Phone1 | varchar(20) | |
| Phone2 | Phone2 | varchar(20) | |
| Fax | Fax | varchar(20) | |
| Balance | Balance | numeric | Saldo konta |
| FrozenFor | FrozenFor | char(1) | Y/N |
| Valid | validFor | char(1) | Y/N |
| GroupCode | GroupCode | int | ID grupy BP |
| SalesPersonCode | SlpCode | int | ID handlowca |
| PayTermsGrpCode | GroupNum | int | Warunki płatności |
| Currency | Currency | varchar(3) | Domyślna waluta |
| Country | Country | varchar(3) | Kod kraju |
| BPLId | BPLId | int | **NA TRANSAKCJACH, nie na BP!** |

### OITM (Items / Produkty)

| Service Layer | SQL Column | Typ | Uwagi |
|---------------|------------|-----|-------|
| ItemCode | ItemCode | varchar(50) | Klucz główny |
| ItemName | ItemName | varchar(100) | Nazwa (angielska w naszym SAP) |
| ForeignName | FrgnName | varchar(100) | Nazwa (polska w naszym SAP — odwrotnie!) |
| BarCode | CodeBars | varchar(254) | EAN/GTIN |
| SupplierCatalogNo | SuppCatNum | varchar(17) | Nr katalogowy dostawcy |
| Manufacturer | FirmCode | int | Kod producenta |
| ItemsGroupCode | ItmsGrpCod | int | Grupa towarowa |
| Valid | validFor | char(1) | Y=aktywny |
| CreateDate | CreateDate | date | Data utworzenia |
| UpdateDate | UpdateDate | date | Ostatnia zmiana |
| Mainsupplier | CardCode | varchar(15) | Domyślny dostawca |
| ItemType | ItemType | char(1) | I=inventory |

### OINV / ORIN / OPCH (Dokumenty — nagłówki)

| Service Layer | SQL Column | Typ | Uwagi |
|---------------|------------|-----|-------|
| DocEntry | DocEntry | int | Klucz wewnętrzny (identyfikator) |
| DocNum | DocNum | int | Numer dokumentu (wyświetlany) |
| CardCode | CardCode | varchar(15) | Kod kontrahenta |
| CardName | CardName | varchar(100) | Nazwa kontrahenta |
| DocDate | DocDate | date | Data dokumentu |
| DocDueDate | DocDueDate | date | Termin płatności |
| TaxDate | TaxDate | date | Data podatkowa |
| DocTotal | DocTotal | numeric | Suma (waluta systemowa) |
| DocTotalFc | DocTotalFC | numeric | Suma (waluta dokumentu) |
| DocCurrency | DocCur | varchar(3) | Waluta dokumentu |
| DocRate | DocRate | numeric | Kurs wymiany |
| Cancelled | CANCELED | char(1) | N=aktywny, Y=anulowany |
| BPL_IDAssignedToInvoice | BPLId | int | ID brancha |
| NumAtCard | NumAtCard | varchar(100) | Nr faktury dostawcy |
| Comments | Comments | text | Komentarz |

### ORDR / OPOR (Zamówienia — nagłówki)

| Service Layer | SQL Column | Typ | Uwagi |
|---------------|------------|-----|-------|
| DocEntry | DocEntry | int | Klucz wewnętrzny |
| DocNum | DocNum | int | Numer dokumentu |
| CardCode | CardCode | varchar(15) | Kontrahent |
| DocStatus | DocStatus | char(1) | O=open, C=closed |
| DocDate | DocDate | date | Data zamówienia |
| DocDueDate | DocDueDate | date | Termin |
| Cancelled | CANCELED | char(1) | N/Y |
| BPLId | BPLId | int | Branch |
| Comments | Comments | text | Komentarz |

### DocumentLines (linie dokumentów — INV1, RDR1, POR1, PCH1)

| Service Layer | SQL Column | Typ | Uwagi |
|---------------|------------|-----|-------|
| LineNum | LineNum | int | Nr linii (od 0) |
| ItemCode | ItemCode | varchar(50) | Kod produktu |
| ItemDescription | Dscription | varchar(200) | Opis pozycji |
| Quantity | Quantity | numeric | Ilość (w UOM sprzedaży!) |
| InventoryQuantity | InvQty | numeric | **Ilość w UOM magazynowym — ZAWSZE używaj tej!** |
| UnitPrice | Price | numeric | Cena jednostkowa |
| LineTotal | LineTotal | numeric | Suma netto linii (waluta systemowa) |
| LineTotalFc | TotalFrgn | numeric | Suma netto linii (waluta dokumentu) |
| VatGroup | VatGroup | varchar(8) | Kod VAT |
| WarehouseCode | WhsCode | varchar(8) | Magazyn |
| ShipDate | ShipDate | date | Data wysyłki |
| RemainingOpenQuantity | OpenQty | numeric | Ilość do realizacji (SO/PO) |
| RemainingOpenInventoryQuantity | OpenInvQty | numeric | Ilość do realizacji w UOM mag. |

### OITW (Stany magazynowe)

| Service Layer | SQL Column | Uwagi |
|---------------|------------|-------|
| ItemCode | ItemCode | Kod produktu |
| WarehouseCode | WhsCode | Kod magazynu |
| InStock | OnHand | Ilość na stanie |
| Committed | IsCommited | Zarezerwowane (na zamówieniach) |
| Ordered | OnOrder | Zamówione (incoming PO) |
| — | — | **Available = OnHand - IsCommited + OnOrder** |

### CRD1 (Adresy kontrahentów)

| Service Layer | SQL Column | Uwagi |
|---------------|------------|-------|
| AddressName | Address | Nazwa adresu |
| AddressType | AdresType | S=ship-to, B=bill-to |
| Street | Street | Ulica |
| City | City | Miasto |
| ZipCode | ZipCode | Kod pocztowy |
| Country | Country | Kod kraju |
| County | County | Powiat/hrabstwo |
| State | State | Województwo/stan |

---

## 6. Enumy i typy danych

### Service Layer ↔ SQL wartości

SAP w Service Layer używa **stringowych enumów**, a w SQL **skróconych kodów**:

| Service Layer | SQL | Znaczenie |
|---------------|-----|-----------|
| `bost_Open` | `O` | Dokument otwarty |
| `bost_Close` | `C` | Dokument zamknięty |
| `bo_ShipTo` | `S` | Adres dostawy |
| `bo_BillTo` | `B` | Adres rozliczeniowy |
| `tYES` | `Y` | Tak / prawda |
| `tNO` | `N` | Nie / fałsz |
| `cCustomer` | `C` | Klient |
| `cSupplier` | `S` | Dostawca |
| `cLid` | `L` | Lead |
| `it_Items` | `I` | Pozycja magazynowa |
| `it_FixedAssets` | `F` | Środek trwały |

### Ważne typy danych

- **Daty** — format ISO: `"2024-06-15"` (Service Layer), `date` w SQL
- **Kwoty** — `numeric` w SQL, JSON number w SL. Zaokrąglanie do 2 miejsc (kwoty) lub 4 (ceny jednostkowe)
- **Cancelled** — **nullable char(1)** — w SQL może być NULL! Używaj `IS DISTINCT FROM 'Y'`
- **BPLId** — `int` w SQL, ale aplikacje często przechowują jako `str` → **zawsze castuj przy porównaniu**

---

## 7. Business Partners (OCRD)

### Tworzenie BP

```json
POST /BusinessPartners
{
    "CardCode": "C10001",
    "CardName": "COMPANY NAME LTD",
    "CardType": "cCustomer",
    "FederalTaxID": "PL5671770667",
    "Currency": "PLN",
    "Country": "PL",
    "Series": 201,
    "BPAddresses": [...],
    "ContactEmployees": [...]
}
```

**Czyszczenie payloadu:**
- Usuń puste stringi z: FederalTaxID, EmailAddress, Phone1
- Usuń wartości sentinel: PayTermsGrpCode=-1 → usuń pole, SalesPersonCode=-1 → usuń pole
- Usuń puste obiekty z ContactEmployees i BPAddresses

### Aktualizacja BP

```
PATCH /BusinessPartners('C10001')
{"EmailAddress": "new@email.com", "Phone1": "+48123456789"}
→ 204 No Content
```

### Szukanie po NIP

NIP w polu `FederalTaxID` (SQL: `LicTradNum`) może mieć prefix "PL" lub nie:

```
GET /BusinessPartners?$filter=contains(FederalTaxID, '5671770667')
```

W SQL:
```sql
SELECT CardCode, CardName FROM OCRD
WHERE LicTradNum = 'PL5671770667' OR LicTradNum = '5671770667'
```

### Serie numeracyjne (nasz SAP)

| Series | Typ | Branch | Tryb |
|--------|-----|--------|------|
| 1 | Customer | Manual | Ręczny CardCode |
| 2 | Supplier | Manual | Ręczny CardCode |
| 201 | Customer | Poland | Automatyczny |
| 202 | Customer | London | Automatyczny |
| 203 | Customer | Midlands | Automatyczny |
| 204 | Supplier | Poland | Automatyczny |
| 205 | Supplier | London | Automatyczny |
| 206 | Supplier | Midlands | Automatyczny |

### NIGDY nie używaj DELETE na sub-kolekcjach!

```
DELETE /BusinessPartners('C10001')/ContactEmployees(123)   → KASUJE CAŁEGO BP!!!
DELETE /BusinessPartners('C10001')/BPAddresses('Ship1')    → KASUJE CAŁEGO BP!!!
```

To jest bug/feature SAP Service Layer. Jedyny bezpieczny sposób usunięcia kontaktu lub adresu:

### B1S-ReplaceCollectionsOnPatch

```python
headers = {"B1S-ReplaceCollectionsOnPatch": "true"}

PATCH /BusinessPartners('C10001')
# Wyślij TYLKO elementy do zachowania — pominięte zostaną usunięte
{
    "ContactEmployees": [
        {"InternalCode": 123},   # ten zostanie
        {"InternalCode": 456}    # ten zostanie
        # InternalCode 789 pominięty → zostanie usunięty
    ]
}
```

**Bez tego headera (domyślne zachowanie PATCH):**
- Istniejące elementy są MERGOWANE, nie zastępowane
- Pominięcie elementu NIE usuwa go — SAP cicho go zachowuje
- Elementy z InternalCode → aktualizacja; bez InternalCode → dodanie nowego

### Adresy — U_ExtAddrGUID

Każdy adres MUSI mieć unikalny `U_ExtAddrGUID` (UUID):
- Generuj `str(uuid.uuid4())` dla KAŻDEGO nowego adresu
- Stare GUID-y mogą „osieroceć" w CRD1 po usunięciu BP
- Przy odtwarzaniu/tworzeniu adresów ZAWSZE nowe UUID

### Adresy — tworzenie parami

Zawsze twórz adresy w parach BILL_TO + SHIP_TO:

```json
{
    "BPAddresses": [
        {
            "AddressName": "HQ",
            "AddressType": "bo_BillTo",
            "Street": "ul. Główna 1",
            "City": "Warszawa",
            "ZipCode": "00-001",
            "Country": "PL",
            "U_ExtAddrGUID": "uuid-here"
        },
        {
            "AddressName": "HQ",
            "AddressType": "bo_ShipTo",
            "Street": "ul. Główna 1",
            "City": "Warszawa",
            "ZipCode": "00-001",
            "Country": "PL",
            "U_ExtAddrGUID": "another-uuid"
        }
    ]
}
```

### Odtwarzanie BP (po przypadkowym usunięciu)

Kolejność MUSI być dokładnie taka:

1. **POST minimalny** — tylko `CardCode`, `CardName`, `CardType`
   - NIE dołączaj BPAddresses/GroupCode/Series → powoduje błędy "already exists"
2. **PATCH ContactEmployees** — najpierw kontakty
3. **PATCH BPAddresses** — z NOWYMI UUID, bez State/CreateDate/CreateTime
4. **PATCH Skalary** — bez: pól adresowych, Frozen/Valid, GroupCode, Series, pól computed
5. **PATCH Frozen + Valid razem** — minimum jedno musi być `tYES`
6. **PATCH Skalary adresowe** — Address, City, ZipCode (po tym jak BPAddresses już istnieją)
7. **PATCH BPBranchAssignment** — na końcu

### Pola których NIGDY nie wysyłaj w PATCH BP

**Read-only / Computed:**
- Balance, AvailableCredit, OpenOrdersBalance, DataVersion, AvarageLate

**Auto-zarządzane:**
- Series, ContactPerson, ShipToDefault, BilltoDefault

**Konfliktowe:**
- GroupCode (może nie istnieć w docelowym systemie)
- Address, City, County, ZipCode, Country na poziomie BP (konflikt z BPAddresses)
- MailAddress, MailCity, MailCountry, MailCounty, MailZipCode
- BillToState, ShipToState

### Frozen / Valid — specjalna obsługa

- SAP wymaga żeby przynajmniej jedno z Frozen/Valid było `tYES`
- Jeśli oba `tNO` → ustaw `Valid=tYES`
- `Frozen=tYES` bez `FrozenTo`/`FrozenFrom` → błąd "Date ranges overlap"
- **ZAWSZE wysyłaj Frozen + Valid w jednym PATCH** (nie osobno!)

---

## 8. Items / Produkty (OITM)

### Wyszukiwanie

```
GET /Items?$filter=BarCode eq '5901234567890'&$select=ItemCode,ItemName
GET /Items?$filter=contains(ItemName, 'widget') or contains(ItemCode, 'widget')&$top=50
GET /Items?$filter=Valid eq 'tYES'
```

SQL:
```sql
-- Po EAN
SELECT ItemCode, ItemName, CodeBars FROM OITM WHERE CodeBars = '5901234567890'

-- Szukaj tekstowo (aktywne)
SELECT ItemCode, ItemName, CodeBars, SuppCatNum
FROM OITM
WHERE (ItemName LIKE '%widget%' OR ItemCode LIKE '%widget%')
  AND validFor = 'Y'

-- Produkty bez EAN
SELECT ItemCode, ItemName FROM OITM
WHERE (CodeBars IS NULL OR CodeBars = '') AND validFor = 'Y'
```

### Tworzenie produktu

```json
POST /Items
{
    "ItemCode": "G001",
    "ItemName": "English Name",
    "ForeignName": "Polska Nazwa",
    "BarCode": "5901234567890",
    "ItemsGroupCode": 100,
    "Manufacturer": 5,
    "SupplierCatalogNo": "SUP-001",
    "U_CNCode": "19059099",
    "ItemWarehouseInfoCollection": [...],
    "ItemPrices": [...],
    "ItemUnitOfMeasurementCollection": [...]
}
```

**UWAGA — nazwy odwrotnie:**
- `ItemName` = nazwa angielska (w naszym SAP)
- `ForeignName` = nazwa polska

### Przypisanie EAN

```
PATCH /Items('G001')
{"BarCode": "5901234567890"}
→ 204 No Content
```

Przed przypisaniem sprawdź duplikaty:
```sql
SELECT ItemCode, ItemName FROM OITM WHERE CodeBars = '5901234567890'
```

### Stany magazynowe

```sql
SELECT w.ItemCode, w.WhsCode, w.OnHand, w.IsCommited, w.OnOrder, wh.WhsName
FROM OITW w
JOIN OWHS wh ON wh.WhsCode = w.WhsCode
WHERE w.ItemCode = 'G001'
```

- `OnHand` = na stanie
- `IsCommited` = zarezerwowane (zamówienia sprzedaży)
- `OnOrder` = zamówione (zamówienia zakupu)
- **Available = OnHand - IsCommited + OnOrder**

### Cenniki

```sql
SELECT p.PriceList, p.Price, p.Currency, pl.ListName
FROM ITM1 p
JOIN OPLN pl ON pl.ListNum = p.PriceList
WHERE p.ItemCode = 'G001' AND p.Price > 0
```

### Ceny specjalne (per kontrahent)

```sql
SELECT ItemCode, CardCode, Price, Currency, FromDate, ToDate
FROM SPP1
WHERE ItemCode = 'G001'
  AND (FromDate IS NULL OR FromDate <= GETDATE())
  AND (ToDate IS NULL OR ToDate >= GETDATE())
```

### Quantity vs InventoryQuantity — KRYTYCZNE

| Pole | Kiedy używać | Dlaczego |
|------|-------------|----------|
| `InventoryQuantity` (SQL: `InvQty`) | Linie faktur/korekt | Ilość w UOM magazynowym — ZAWSZE to! |
| `Quantity` | NIGDY do obliczeń stanów | Ilość w UOM sprzedaży — może się różnić! |
| `RemainingOpenInventoryQuantity` (SQL: `OpenInvQty`) | Linie SO/PO | Pozostało do realizacji |
| `InStock` (SQL: `OnHand`) | Stany mag. | Bieżąca ilość na magazynie |

Produkt może mieć UOM sprzedaży ≠ UOM magazynowy (np. sprzedawany w kartonach, liczony w sztukach).

### Alternatywne numery katalogowe (OSCN)

```json
POST /AlternateCatNum
{
    "ItemCode": "E90024",
    "CardCode": "V10001",
    "Substitute": "E90024",
    "IsDefault": "tYES"
}
```

---

## 9. Faktury i dokumenty

### Tworzenie faktury zakupu

```json
POST /PurchaseInvoices
{
    "CardCode": "V10001",
    "NumAtCard": "FV/2024/001",
    "DocDate": "2024-06-15",
    "DocDueDate": "2024-07-15",
    "TaxDate": "2024-06-15",
    "VatDate": "2024-06-10",
    "DocCurrency": "PLN",
    "BPL_IDAssignedToInvoice": 3,
    "DocumentLines": [
        {
            "ItemCode": "G001",
            "ItemDescription": "Nazwa produktu",
            "Quantity": 10,
            "UnitPrice": 25.50,
            "VatGroup": "D23"
        }
    ]
}
```

**Kluczowe pola:**
- `NumAtCard` — numer faktury dostawcy (do deduplikacji)
- `VatDate` — data obowiązku VAT (= data dostawy/wykonania usługi, pole P_6 z KSeF)
- `TaxDate` — data podatkowa (zwykle = DocDate)
- `BPL_IDAssignedToInvoice` — **obowiązkowe**, dla Polski = 3
- `VatGroup` — **NIE `TaxCode`!** (patrz sekcja VAT)

### POST PurchaseInvoices — może nie zwrócić DocNum

```python
result = await client.post("/PurchaseInvoices", json=payload)
doc_entry = result.json().get("DocEntry")
doc_num = result.json().get("DocNum")

if not doc_num:
    # Fallback — pobierz przez DocEntry
    doc = await client.get(f"/PurchaseInvoices({doc_entry})?$select=DocNum")
    doc_num = int(doc.json()["DocNum"])  # Normalizuj do int!
```

### Weryfikacja zaokrągleń przez Draft

VAT per-linia może powodować różnicę ±0.01–0.05 w sumie dokumentu.

**Strategia:**
1. Utwórz Draft: `POST /Drafts` z `DocObjectCode: "oPurchaseInvoices"`
2. Pobierz Draft → weź SAP-ową sumę (`DocTotalFc` lub `DocTotal`)
3. Porównaj z oczekiwaną (tolerancja ±0.05)
4. Ustaw `DocTotal` na wartość SAP-ową
5. Usuń Draft: `DELETE /Drafts({doc_entry})`
6. POST prawdziwą fakturę z skorygowanym `DocTotal`

```python
# Tworzenie draftu
draft_payload = {**invoice_payload, "DocObjectCode": "oPurchaseInvoices"}
draft = await client.post("/Drafts", json=draft_payload)
draft_entry = draft.json()["DocEntry"]

# Pobranie SAP-owej sumy
draft_doc = await client.get(f"/Drafts({draft_entry})")
sap_total = draft_doc.json().get("DocTotalFc") or draft_doc.json()["DocTotal"]

# Korekta
if abs(sap_total - expected_total) <= 0.05:
    invoice_payload["DocTotal"] = sap_total

# Cleanup + POST
await client.delete(f"/Drafts({draft_entry})")
result = await client.post("/PurchaseInvoices", json=invoice_payload)
```

### Linie ujemne (rabaty/korekty)

```python
# DOBRZE: ujemna ilość + dodatnia cena
{"Quantity": -1, "UnitPrice": 50.00, "VatGroup": "D23"}

# ŹLE: powoduje błąd SAP -5002
{"Quantity": 1, "UnitPrice": -50.00, "VatGroup": "D23"}
```

### Status anulowania

```
# OData — stringowy enum, NIE boolean
$filter=Cancelled eq 'tNO'

# SQL — nullable char, używaj IS DISTINCT FROM
WHERE CANCELED IS DISTINCT FROM 'Y'
-- lub: WHERE (CANCELED IS NULL OR CANCELED = 'N')
```

### Deduplikacja faktur

Klucz złożony: `CardCode` + `NumAtCard`:
```
GET /PurchaseInvoices?$filter=CardCode eq 'V10001' and NumAtCard eq 'FV/2024/001' and Cancelled eq 'tNO'
```

Lub przez UDF:
```
GET /PurchaseInvoices?$filter=U_KSeFid eq 'KSeF-REF-123' and Cancelled eq 'tNO'
```

### Aktualizacja UDF na istniejącym dokumencie

```
PATCH /PurchaseInvoices({doc_entry})
{"U_KSeFid": "KSeF-REF-123"}
→ 204 No Content
```

---

## 10. Zamówienia sprzedaży i zakupu

### Otwarte zamówienia

**Service Layer:**
```
GET /Orders?$filter=DocumentStatus eq 'bost_Open' and Cancelled eq 'tNO'
GET /PurchaseOrders?$filter=DocumentStatus eq 'bost_Open' and Cancelled eq 'tNO'
```

**SQL:**
```sql
-- Otwarte zamówienia sprzedaży
SELECT o.DocEntry, o.DocNum, o.CardCode, o.DocDueDate,
       l.ItemCode, l.Quantity, l.OpenQty, l.ShipDate, l.WhsCode
FROM ORDR o
JOIN RDR1 l ON l.DocEntry = o.DocEntry
WHERE o.DocStatus = 'O' AND (o.CANCELED IS NULL OR o.CANCELED = 'N')

-- Otwarte zamówienia zakupu
SELECT o.DocEntry, o.DocNum, o.CardCode, o.DocDueDate,
       l.ItemCode, l.Quantity, l.OpenQty, l.ShipDate, l.WhsCode
FROM OPOR o
JOIN POR1 l ON l.DocEntry = o.DocEntry
WHERE o.DocStatus = 'O' AND (o.CANCELED IS NULL OR o.CANCELED = 'N')
```

### DocumentStatus — mapowanie

| SQL `DocStatus` | Service Layer | Znaczenie |
|-----------------|---------------|-----------|
| `O` | `bost_Open` | Otwarty |
| `C` | `bost_Close` | Zamknięty |

---

## 11. VAT / Kody podatkowe

### KRYTYCZNE: `VatGroup`, NIE `TaxCode`

W `DocumentLines` pole nazywa się **`VatGroup`** (nie `TaxCode`).

Endpoint `SalesTaxCodes` zwraca PUSTY wynik. Kody VAT są w endpoincie **`VatGroups`**.

### Struktura kodów VAT (nasz SAP)

| Prefix | Kierunek | Przykłady |
|--------|----------|-----------|
| D | VAT naliczony (zakupy) | D23, D08, D05, D00, Dzw |
| S | Standard output | S23 |
| F | VAT należny (sprzedaż) | F23, F08, F00 |
| FUt | Eksport towarów | FUt (0% eksport) |
| FUu | Eksport usług | FUu (0% usługi za granicą) |
| WDT0 | Wewnątrzwspólnotowa | WDT0 (0% WDT) |
| EXP0 | Eksport poza UE | EXP0 (0% eksport) |

### Kody VAT zakupowe (potwierdzone w naszym SAP)

| Stawka | VatGroup | Opis |
|--------|----------|------|
| 23% | D23 | Standardowy VAT |
| 8% | D08 | Obniżony (żywność itp.) |
| 5% | D05 | Obniżony (książki itp.) |
| 0% krajowe | D00 | Krajowe 0% |
| Zwolniony | Dzw | VAT zwolniony |

### Mapowanie KSeF → SAP

| KSeF P_12 | SAP VatGroup | Pole KSeF P_13 |
|-----------|-------------|----------------|
| 23 | D23 | P_13_1 |
| 8 | D08 | P_13_2 |
| 5 | D05 | P_13_3 |
| 0 KR | D00 / F00 | P_13_6_1 (krajowe 0%) |
| 0 WDT | WDT0 | P_13_6_2 (WDT) |
| 0 EX | EXP0 | P_13_6_3 (eksport) |
| zw | Dzw | P_13_7 (zwolniony) |
| np / np I | — | P_13_8 (nie podlega) |

### Logika eksportowa FUt

Kod `FUt` (eksport towarów) rozwiązuje się różnie w zależności od kraju kupującego:

```python
def _resolve_line_vat_mapping(vat_group: str, buyer_country: str) -> str:
    if vat_group == "FUt":
        if buyer_country in EU_COUNTRIES:
            return "0 WDT"   # P_13_6_2
        else:
            return "0 EX"    # P_13_6_3
```

Mapowanie per VatGroup:
- **FUt** = eksport towarów → `0 EX` (P_13_6_3) lub `0 WDT` (P_13_6_2) zależnie od kraju
- **FUu** = usługi poza terytorium → `np I` (P_13_8)
- **F00** = krajowe 0% → `0 KR` (P_13_6_1)

---

## 12. Waluty

### Zasady

1. **Zawsze ustawiaj `DocCurrency`** w payloadzie — domyślnie bierze walutę dostawcy
2. **Używaj `DocTotalFc`** (waluta dokumentu), NIE `DocTotal` (waluta systemowa)
3. **Używaj `RowTotalFC` / `NetTaxAmountFC`** per linia, NIE `LineTotal` / `TaxTotal`

### Dokument w walucie obcej

```json
{
    "DocCurrency": "GBP",
    "DocRate": 5.1234,
    "DocumentLines": [
        {
            "UnitPrice": 100.00,
            "VatGroup": "D23"
        }
    ]
}
```

- `DocTotal` = kwota w walucie systemowej (PLN u nas)
- `DocTotalFc` = kwota w walucie dokumentu (GBP)
- `DocRate` = kurs wymiany (tylko dla walut obcych)

### Kody walut

SAP może używać niestandardowych symboli walut (np. `$` zamiast `USD`). Sprawdź mapowanie w konfiguracji SAP.

### Kurs wymiany w KSeF XML (FA(3))

`KursWaluty` znajduje się w **FaWiersz** (per-linia), NIE na poziomie nagłówka Fa.

---

## 13. User-Defined Fields (UDF)

Wszystkie UDF mają prefix `U_`. Nazwy są **case-sensitive** w Service Layer!

### UDFs na OCRD (Business Partners)

| UDF | Typ | Przeznaczenie |
|-----|-----|---------------|
| `U_InvEmail` | varchar | Email do faktur |
| `U_StatEmail` | varchar | Email do wyciągów |
| `U_CompRegNo` | varchar | Nr rejestrowy firmy (np. KRS, UK Company Number) |
| `U_CompStatus` | char(1) | Status firmy: A=aktywna, D=rozwiązana, L=likwidacja, X=administracja |
| `U_CompanyType` | char(1) | Typ firmy: L=Limited, S=Sole-trader |
| `U_b2bbranch` | varchar | Kod brancha B2B |
| `U_b2bclient` | varchar | Kod klienta B2B |
| `U_OrdFreq` | int | Częstotliwość zamówień (dni) |
| `U_OpInvAllow` | int | Dozwolone otwarte faktury |
| `U_DunLevAllowed` | int | Dozwolony poziom monitów |

### UDFs na CRD1 (Adresy BP)

| UDF | Typ | Przeznaczenie |
|-----|-----|---------------|
| `U_ExtAddrGUID` | varchar | UUID adresu — MUSI być unikalny globalnie! |
| `U_BPLatitude` | varchar | Szerokość GPS (uwaga: varchar, nie numeric!) |
| `U_BPLongitude` | varchar | Długość GPS |
| `U_BPOpen` | varchar | Godzina otwarcia |
| `U_BPTill` | varchar | Godzina zamknięcia |
| `U_VehDelType` | varchar | Typ pojazdu dostawczego |
| `U_RecomDelDay` | varchar | Rekomendowany dzień dostawy |

### UDFs na OITM (Items)

| UDF | Typ | Przeznaczenie |
|-----|-----|---------------|
| `U_CNCode` | varchar | Kod CN/HS (celny) — **CASE-SENSITIVE!** Dokładnie `U_CNCode` |
| `U_Category` | varchar | Kategoria produktu (backslash-separated: `"Cakes\\Fondant"`) |

Parsowanie kategorii:
```python
main_category = u_category.split("\\")[0] if u_category else None
```

### UDFs na OINV / OPCH (Faktury)

| UDF | Typ | Przeznaczenie |
|-----|-----|---------------|
| `U_KSeFid` | varchar | Nr referencyjny KSeF (deduplikacja) |
| `U_OrderType` | varchar | Klasyfikacja typu zamówienia |

### UDFs na ORDR (Zamówienia sprzedaży — transport)

| UDF | Typ | Przeznaczenie |
|-----|-----|---------------|
| `U_trtractor` | varchar | Nr rejestracyjny ciągnika (może mieć spacje → strip!) |
| `U_trtrailer` | varchar | Nr rejestracyjny naczepy |
| `U_trloadplace1` | varchar | Miejsce załadunku 1 |
| `U_trloadplace2` | varchar | Miejsce załadunku 2 |
| `U_trunloadingpl1` | varchar | Miejsce rozładunku 1 |
| `U_trunloadingpl2` | varchar | Miejsce rozładunku 2 |
| `U_trlodatepl1` | varchar | Data załadunku 1 |
| `U_trlodatepl2` | varchar | Data załadunku 2 |
| `U_trunlodatepl1` | varchar | Data rozładunku 1 |
| `U_trunlodatepl2` | varchar | Data rozładunku 2 |
| `U_trunlotime1` | varchar | Czas rozładunku 1 |
| `U_trunlotime2` | varchar | Czas rozładunku 2 |
| `U_trremarks` | varchar | Uwagi transportowe |

### UDFs na UDT @VEHLO (Pojazdy)

| UDF | Typ | Przeznaczenie |
|-----|-----|---------------|
| `U_defdriver` | varchar | Domyślny kierowca (branch ogólny) |
| `U_defdriverm` | varchar | Domyślny kierowca (**Midlands — inne pole!**) |

### UDFs na Contact Employees

| UDF | Typ | Przeznaczenie |
|-----|-----|---------------|
| `U_ConCity` | varchar | Miasto kontaktu |
| `U_ConPostcode` | varchar | Kod pocztowy kontaktu |
| `U_OfficerRole` | varchar | Rola (D=director, S=secretary) |
| `U_AppointedOn` | varchar | Data powołania |

---

## 14. User-Defined Tables (UDT)

| SAP UDT | Prefix w SL | Zawartość |
|---------|-------------|-----------|
| @DRILO | `U_DRILO` | Ładunki kierowców |
| @DRIMI | `U_DRIMI` | Pozycje kierowców |
| @VEHLO | `U_VEHLO` | Ładunki pojazdów |
| @VEHMI | `U_VEHMI` | Pozycje pojazdów |

Dostęp przez Service Layer:
```
GET /U_DRILO
GET /U_VEHLO?$filter=...
```

---

## 15. Obsługa błędów

### Hierarchia wyjątków (rekomendowana)

```python
class SAPError(Exception):
    error_code: int
    message: str
    technical_details: str
    field_name: str | None

class SAPConnectionError(SAPError): ...    # Sieć/DNS/503
class SAPAuthenticationError(SAPError): ... # 401
class SAPValidationError(SAPError): ...     # 400
class SAPDuplicateError(SAPError): ...      # Element już istnieje (409)
class SAPServiceError(SAPError): ...        # 500+
class SAPTimeoutError(SAPError): ...        # Timeout
class SAPRequiredFieldError(SAPError): ...  # Brak wymaganego pola
```

### Mapowanie HTTP → wyjątki

| HTTP Status | Wyjątek | Akcja |
|-------------|---------|-------|
| 401 | SAPAuthenticationError | Re-login + retry |
| 400 | SAPValidationError | Loguj, pokaż użytkownikowi |
| 409 | SAPDuplicateError | Sprawdź istniejący, skip/update |
| 422 | SAPRequiredFieldError | Sprawdź payload |
| 503 | SAPConnectionError | Retry z backoff |
| 504 | SAPTimeoutError | Retry z dłuższym timeout |
| 500+ | SAPServiceError | Loguj, retry z backoff |

### Strategia retry (dwa poziomy)

**Poziom 1: HTTP (sieć/timeout)**
- 3 próby z exponential backoff: ~1s → ~2s → ~4s (±25% jitter)
- Retry na: HTTPError, TimeoutException, ConnectError, NetworkError

**Poziom 2: Sesja (401)**
- Max 2 retry z 2s backoff
- Na 401: re-login → powtórz request

**Błędy 500 przy paginacji:**
- Backoff: 0.5s → 1.5s → 3s
- Po 3 próbach: **POMIŃ stronę** (nie przerywaj całego synca)

### Parsowanie błędów

```python
def parse_sap_error(response) -> SAPError:
    data = response.json()
    error = data.get("error", {})
    code = error.get("code", -1)
    message = error.get("message", {}).get("value", "Unknown error")

    if response.status_code == 401:
        return SAPAuthenticationError(code, message)
    if "already exists" in message.lower() or "duplicate" in message.lower():
        return SAPDuplicateError(code, message)
    if "is required" in message.lower() or "cannot be empty" in message.lower():
        return SAPRequiredFieldError(code, message)
    if response.status_code >= 500:
        return SAPServiceError(code, message)
    return SAPValidationError(code, message)
```

---

## 16. Krytyczne pułapki

### 1. DELETE na sub-kolekcjach BP = KASUJE CAŁEGO BP
Używaj `PATCH` z headerem `B1S-ReplaceCollectionsOnPatch: true`.

### 2. VatGroup, nie TaxCode
W DocumentLines pole to `VatGroup`. Endpoint `SalesTaxCodes` jest pusty → używaj `VatGroups`.

### 3. InventoryQuantity, nie Quantity
Na liniach faktur ZAWSZE `InventoryQuantity` (UOM magazynowy). `Quantity` to UOM sprzedaży.

### 4. DocTotalFc, nie DocTotal
Dla walut obcych `DocTotal` jest w walucie systemowej. Używaj `DocTotalFc` (waluta dokumentu).

### 5. POST PurchaseInvoices może nie zwrócić DocNum
Zawsze miej fallback GET po DocEntry.

### 6. Frozen + Valid — razem w jednym PATCH
SAP wymaga min. jednego `tYES`. Nie wysyłaj osobno.

### 7. Cancelled jest nullable
W SQL `CANCELED` może być NULL. Używaj `IS DISTINCT FROM 'Y'` lub `(CANCELED IS NULL OR CANCELED = 'N')`.

### 8. Puste stringi vs NULL
Wiele pól może być zarówno NULL jak i `''`. Zawsze sprawdzaj oba: `WHERE (field IS NULL OR field = '')`.

### 9. Ujemne linie: ujemna ilość, dodatnia cena
Ujemny UnitPrice powoduje błąd -5002. Używaj ujemnego Quantity.

### 10. Sesja wygasa po 30 minutach
Proaktywnie odświeżaj. Dla wielu instancji — Redis.

### 11. PATCH zwraca 204 No Content
Nie parsuj body odpowiedzi z PATCH.

### 12. Metadane OData przed PATCH
Usuń `odata.metadata`, `odata.etag`, `odata.type` z pobranych danych zanim użyjesz w PATCH.

### 13. Rate limiting — 200ms między stronami
Bez opóźnień SAP zwraca 500.

### 14. BPL_IDAssignedToInvoice jest obowiązkowe
Musi być na każdym dokumencie. Polska = 3.

### 15. U_CNCode jest case-sensitive
Dokładnie `U_CNCode`, nie `u_cncode` ani `U_CNCODE`.

### 16. NIP w FederalTaxID może mieć prefix "PL"
Szukaj zarówno z prefixem jak i bez: `contains(FederalTaxID, '5671770667')`.

### 17. U_BPLatitude/Longitude to varchar, nie numeric
Mogą zawierać puste stringi. Konwertuj bezpiecznie: `TRY_CAST(NULLIF(U_BPLatitude, '') AS NUMERIC)`.

### 18. BPLId to int w SQL ale str w aplikacjach
Zawsze castuj: `WHERE BPLId = CAST(:branch_id AS INTEGER)`.

### 19. Bezpieczeństwo synca danych
Przy synchronizacji produktów — dezaktywuj tylko jeśli nowy fetch zwrócił ≥50% dotychczasowych (chroni przed masową dezaktywacją przy częściowym błędzie API).

### 20. Zabezpieczenie przed „zawieszonym" syncem
Oznaczaj synce jako failed po 2h. Zawsze ustawiaj `started_at` przed rozpoczęciem.

### 21. Ścieżki Windows w systemd — forward slashes
W `EnvironmentFile` systemd backslash = escape. Używaj forward slashes:
```bash
SSH_REMOTE_SCRIPT_PATH=D:/Scripts/app    # DOBRZE
SSH_REMOTE_SCRIPT_PATH=D:\Scripts\app    # ŹLE
```

---

## 17. Endpointy Service Layer — referencja

| Endpoint | Metody HTTP | Opis |
|----------|-------------|------|
| `/Login` | POST | Autentykacja |
| `/Logout` | POST | Zamknięcie sesji |
| `/$metadata` | GET | Metadane OData / health check |
| `/Items` | GET, POST, PATCH | Produkty |
| `/BusinessPartners` | GET, POST, PATCH | Kontrahenci |
| `/BusinessPartnerGroups` | GET | Grupy kontrahentów |
| `/PaymentTermsTypes` | GET | Warunki płatności (OCTG) |
| `/Invoices` | GET, PATCH | Faktury sprzedaży |
| `/PurchaseInvoices` | GET, POST, PATCH | Faktury zakupu |
| `/CorrectionPurchaseInvoice` | GET, PATCH | Korekty zakupu |
| `/CreditNotes` | GET | Korekty sprzedaży |
| `/Drafts` | GET, POST, DELETE | Drafty (do weryfikacji zaokrągleń) |
| `/Orders` | GET, PATCH | Zamówienia sprzedaży |
| `/PurchaseOrders` | GET | Zamówienia zakupu |
| `/SalesForecast` | POST, PATCH | Prognozy sprzedaży |
| `/AlternateCatNum` | GET, POST | Alt. numery katalogowe (OSCN) |
| `/VatGroups` | GET | Kody VAT (NIE SalesTaxCodes!) |
| `/Manufacturers` | GET | Producenci |
| `/UnitOfMeasurements` | GET | Jednostki miary |
| `/UnitOfMeasurementGroups` | GET | Grupy jednostek miary |
| `/Warehouses` | GET | Magazyny |
| `/SalesPersons` | GET | Handlowcy |
| `/EmployeesInfo` | GET | Pracownicy |

### Dostęp do UDT

```
GET /U_DRILO         # @DRILO
GET /U_VEHLO         # @VEHLO
```

---

## 18. Połączenie bezpośrednie z SQL Server

### Sync Agent

Agent synchronizacji działa na serwerze SAP (`C:\SAPSync\sync_agent.py`):
- Odczytuje z SQL Server bezpośrednio
- Replikuje dane do PostgreSQL
- Sync co 15 minut (incremental) + on-demand
- On-demand: tabela `sync_requests` w PostgreSQL

### Kluczowe różnice SQL Server vs Service Layer

| Aspekt | Service Layer | SQL Server |
|--------|---------------|------------|
| Nazwy pól | CamelCase (`ItemCode`) | CamelCase (`ItemCode`) |
| Enumy | Stringowe (`tYES`, `bost_Open`) | Skrócone (`Y`, `O`) |
| Adresy | Osobny typ `bo_ShipTo` | Kolumna `AdresType = 'S'` |
| Sub-kolekcje | Zagnieżdżone JSON | Osobne tabele z JOIN |
| Paginacja | `$skip`/`$top` + `odata.nextLink` | `OFFSET`/`FETCH NEXT` |
| UDF | `U_FieldName` w JSON | Kolumna `U_FieldName` w tabeli |

### Przydatne zapytania SQL

```sql
-- Ile produktów ma CN code?
SELECT COUNT(*) FROM OITM WHERE U_CNCode IS NOT NULL AND U_CNCode != ''
-- Wynik w naszym SAP: ~3717 z 7346 (ok. 50%)

-- Aktywni klienci z emailem do faktur
SELECT CardCode, CardName, U_InvEmail, Balance
FROM OCRD
WHERE CardType = 'C' AND (validFor = 'Y' OR validFor IS NULL)
  AND U_InvEmail IS NOT NULL AND U_InvEmail != ''
  AND (FrozenFor IS NULL OR FrozenFor != 'Y')

-- Otwarte zamówienia transportowe (E90024)
SELECT o.DocEntry, o.DocNum, o.CardCode, o.CardName,
       o.U_trtractor, o.U_trtrailer,
       o.U_trloadplace1, o.U_trunloadingpl1
FROM ORDR o
JOIN RDR1 l ON l.DocEntry = o.DocEntry
WHERE l.ItemCode = 'E90024'
  AND o.BPLId = 3
  AND o.DocStatus = 'O'
  AND (o.CANCELED IS NULL OR o.CANCELED != 'Y')
GROUP BY o.DocEntry, o.DocNum, o.CardCode, o.CardName,
         o.U_trtractor, o.U_trtrailer,
         o.U_trloadplace1, o.U_trunloadingpl1
HAVING COUNT(DISTINCT l.ItemCode) = 1  -- Tylko czyste zamówienia transportowe
```

---

*Ostatnia aktualizacja: 2026-03-23*
