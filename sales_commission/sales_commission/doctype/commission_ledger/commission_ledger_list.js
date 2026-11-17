const STATUS_COLORS = {
	Advance: "blue",
	"Moved to Invoice": "gray",
	Earned: "orange",
	"To Deduct": "orange",
	Paid: "green",
	Deducted: "green",
	Cancelled: "red",
};

frappe.listview_settings["Commission Ledger"] = {
	get_indicator(doc) {
		return [__(doc.status), STATUS_COLORS[doc.status], `status,=,${doc.status}`];
	},
};
