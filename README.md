### Sales Commission

Sales commission calculation on Sales Orders and Invoices

### What it does

Works out the sales commission of every Sales Order and Sales Invoice line, and records what each sales person has earned, payment by payment, in the **Commission Ledger**.

- **Rate**: the line's **Item Commission %**, else the **Commission Rate** of the Sales Person (per sales person on the Sales Team).
- **Classification**: each line is New or Renewal, judged by the customer's earlier sales in the same company and Item Group. **Commission Settings > Classification Factors** holds one factor for New and one for Renewal.
- **Formula**: net amount x % x classification factor. After the Sales Person's annual target, the **Escalator Multiplier** of Commission Settings applies on top.
- **Annual target**: the sum of the Sales Person's **Targets** rows (Sales Person > Targets) of the fiscal year that have no Item Group. No such row (or a sum of 0) means no escalator. A payment that crosses the target stays at 1.0 and the following payments get the multiplier.
- **Flow**: validating an order or invoice classifies every line and works out its commission. A payment, a credit note or a cancellation then syncs the ledger in the same request: the sync compares what the document should have earned with what the ledger holds and posts only the difference.
- **Ledger rows are never edited.** A correction is a new row. A row whose source (payment, credit note, order or invoice) was cancelled is marked `is_cancelled` together with its reverse row, like a GL Entry.

### Payroll

- **Commission Settings > Commission Salary Component** is the earning that carries the commission on the Salary Slip.
- A Salary Slip adds one row for that component: the sum of the employee's Commission Ledger rows (same company, not cancelled) dated between the slip's Start and End Date that are not paid on another slip. A zero or negative sum adds no row, and those rows wait for a later slip. If the component is not set, no row is added and a warning shows when there is commission to pay.
- Submitting the slip stamps those rows with the slip (**Salary Slip** on the ledger row). Cancelling the slip clears the stamp.
- If a payment, invoice, order or credit note is cancelled after its rows were paid on a submitted slip, those rows are not marked cancelled. A normal Reversal row (unpaid, dated the day of the cancel) is posted, so a later slip deducts it.
- The slip class is `CommissionSalarySlip`, built on Kartoza's `CustomSalarySlip`. Frappe v15 uses only the last app's Salary Slip override, so **sales_commission must come after kartoza in the installed apps** (`frappe`, `erpnext`, `hrms`, `kartoza`, `sales_commission`). Migrate prints a warning when the order is wrong.

### Decisions

- No daily job and no recalculate button. A failed sync is logged in the Error Log and never blocks the user's save.
- Documents made before the app was installed never earn.
- A document with no sales person creates no ledger rows. This includes credit notes and deductions.
- Lines at 0% create no ledger rows and do not count toward the target.
- Only Payment Entries count as payments. POS, write-off, loyalty and Journal Entries do not.
- A Payment Entry against a Sales Order is an advance. It moves to the invoice when the invoice allocates it.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app sales_commission
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/sales_commission
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
