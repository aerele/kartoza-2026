// Copyright (c) 2026, Aerele and contributors
// For license information, please see license.txt

frappe.ui.form.on("Customer", {
	setup(frm) {
		frm.set_query("sales_person", "sales_team", () => ({ filters: { is_group: 0 } }));
	},
});
