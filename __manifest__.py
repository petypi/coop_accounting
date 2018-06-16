# -*- coding: utf-8 -*-

{
    "name": "Copia Accounting",
    "summary": """
        Customizations to Accounting for Copia""",
    "description": """
    Adds the following fields to Account Payments:
        * Reference Number
        * Till Number
        * Mobile Money System, eg. M-PESA and Airtel Money
    """,
    "author": "Anthony Darwesh, A. Muratha",
    "website": "http://www.copiakenya.com/",
    "category": "Accounting",
    "version": "0.1",
    "depends": ["base", "account", "l10n_copia_coa", "account_accountant", "account_invoicing"],
    "data": [
        "views/account_payment_view.xml",
        "views/account_invoice_view.xml",
        "views/menu.xml",
        "views/account_mpesa.xml",
        "data/accounting_setup.yml",
        "data/journals_data.xml",
        "report/payment_receipt_template.xml",
        "wizard/account_invoice_refund_view.xml",
        "data/scheduler_data.xml",
        "views/supplier_invoice_feedback_view.xml"
    ],
    "demo": [
    ],
    "installable": True,
}
