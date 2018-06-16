# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
from datetime import datetime
from dateutil.relativedelta import relativedelta
from dateutil.parser import parse

from odoo import models, api, _
from odoo.tools.misc import format_date
from odoo.tools import float_is_zero

import json
_logger = logging.getLogger(__name__)


class report_account_aged_partner_copia(models.AbstractModel):
    _name = "account.aged.partner.copia"
    _description = "Aged Partner Balances Copia"
    _inherit = 'account.aged.receivable'

    def get_report_name(self):
        return _("Partner Ageing Report : Customers")

    def get_periods(self):
        periods = []
        periods.append({
            'label' : '0-7 days',
            'days_min' : 0,
            'days_max' : 7,
        })
        periods.append({
            'label' : '8-14 days',
            'days_min' : 8,
            'days_max' : 14,
        })
        periods.append({
            'label' : '15-30 days',
            'days_min' : 15,
            'days_max' : 30,
        })
        periods.append({
            'label' : '31-60 days',
            'days_min' : 31,
            'days_max' : 60,
        })
        periods.append({
            'label' : '61-90 days',
            'days_min' : 61,
            'days_max' : 90,
        })
        periods.append({
            'label' : '> 91 days',
            'days_min' : 91,
            'days_max' : False,
        })
        return periods
    
    def _get_partner_move_lines(self, account_type, date_from, target_move):
        periods = self.get_periods()
        #reverse the order
        periods.reverse()
        start = parse(date_from).strptime(date_from, "%Y-%m-%d")
        for i in periods:
            max_date = start - relativedelta(days=i['days_min'])
            min_date = False if not i['days_max'] else start - relativedelta(days=i['days_max'])
            i.update({
                'max_date' : max_date.strftime('%Y-%m-%d'),
                'min_date' : min_date and min_date.strftime('%Y-%m-%d'),
            })
        #_logger.info('periods are %s', periods)
        res = []
        total = []
        cr = self.env.cr
        user_company = self.env.user.company_id.id
        move_state = ['draft', 'posted']
        if target_move == 'posted':
            move_state = ['posted']
        arg_list = (tuple(move_state), tuple(account_type))
        #build the reconciliation clause to see what partner needs to be printed
        reconciliation_clause = '(l.reconciled IS FALSE)'
        cr.execute('SELECT debit_move_id, credit_move_id FROM account_partial_reconcile where create_date > %s', (date_from,))
        reconciled_after_date = []
        for row in cr.fetchall():
            reconciled_after_date += [row[0], row[1]]
        if reconciled_after_date:
            reconciliation_clause = '(l.reconciled IS FALSE OR l.id IN %s)'
            arg_list += (tuple(reconciled_after_date),)
        arg_list += (date_from, user_company)
        query = '''
            SELECT DISTINCT l.partner_id, UPPER(res_partner.name)
            FROM account_move_line AS l left join res_partner on l.partner_id = res_partner.id, account_account, account_move am
            WHERE (l.account_id = account_account.id)
                AND (l.move_id = am.id)
                AND (am.state IN %s)
                AND (account_account.internal_type IN %s)
                AND ''' + reconciliation_clause + '''
                AND (l.date <= %s)
                AND l.company_id = %s
            ORDER BY UPPER(res_partner.name)'''
        #print(query % arg_list)
        cr.execute(query, arg_list)

        partners = cr.dictfetchall()
        # put a total of 0
        for i in range(7):
            total.append(0)

        # Build a string like (1,2,3) for easy use in SQL query
        partner_ids = [partner['partner_id'] for partner in partners if partner['partner_id']]
        lines = dict((partner['partner_id'] or False, []) for partner in partners)
        #_logger.info('lines are %s', lines)
        if not partner_ids:
            return [], [], []

        # This dictionary will store the not due amount of all partners
        undue_amounts = {}
        query = '''SELECT l.id
                FROM account_move_line AS l, account_account, account_move am
                WHERE (l.account_id = account_account.id) AND (l.move_id = am.id)
                    AND (am.state IN %s)
                    AND (account_account.internal_type IN %s)
                    AND (COALESCE(l.date_maturity,l.date) > %s)\
                    AND ((l.partner_id IN %s) OR (l.partner_id IS NULL))
                AND (l.date <= %s)
                AND l.company_id = %s'''
        cr.execute(query, (tuple(move_state), tuple(account_type), date_from, tuple(partner_ids), date_from, user_company))
        aml_ids = cr.fetchall()
        aml_ids = aml_ids and [x[0] for x in aml_ids] or []
        for line in self.env['account.move.line'].browse(aml_ids):
            partner_id = line.partner_id.id or False
            if partner_id not in undue_amounts:
                undue_amounts[partner_id] = 0.0
            line_amount = line.balance
            if line.balance == 0:
                continue
            for partial_line in line.matched_debit_ids:
                if partial_line.max_date <= date_from:
                    line_amount += partial_line.amount
            for partial_line in line.matched_credit_ids:
                if partial_line.max_date <= date_from:
                    line_amount -= partial_line.amount
            if not self.env.user.company_id.currency_id.is_zero(line_amount):
                undue_amounts[partner_id] += line_amount
                lines[partner_id].append({
                    'line': line,
                    'amount': line_amount,
                    'period': i+1,
                })

        # Use one query per period and store results in history (a list variable)
        # Each history will contain: history[1] = {'<partner_id>': <partner_debit-credit>}
        history = []
        for i in periods:
            args_list = (tuple(move_state), tuple(account_type), tuple(partner_ids),)
            dates_query = '(COALESCE(l.date_maturity,l.date)'

            if i['min_date'] and i['max_date']:
                dates_query += ' BETWEEN %s AND %s)'
                args_list += (i['min_date'], i['max_date'])
            elif i['max_date']:
                dates_query += ' <= %s)'
                args_list += (i['max_date'],)
            else:
                dates_query += ' >= %s)'
                args_list += (i['min_date'],)
            
            args_list += (date_from, user_company)

            query = '''SELECT l.id
                    FROM account_move_line AS l, account_account, account_move am
                    WHERE (l.account_id = account_account.id) AND (l.move_id = am.id)
                        AND (am.state IN %s)
                        AND (account_account.internal_type IN %s)
                        AND ((l.partner_id IN %s) OR (l.partner_id IS NULL))
                        AND ''' + dates_query + '''
                    AND (l.date <= %s)
                    AND l.company_id = %s'''
            #print('query is {} and args_list is {}'.format(query % args_list, args_list))
            cr.execute(query, args_list)
            partners_amount = {}
            aml_ids = cr.fetchall()
            aml_ids = aml_ids and [x[0] for x in aml_ids] or []
            for line in self.env['account.move.line'].browse(aml_ids):
                partner_id = line.partner_id.id or False
                if partner_id not in partners_amount:
                    partners_amount[partner_id] = 0.0
                line_amount = line.balance
                if line.balance == 0:
                    continue
                for partial_line in line.matched_debit_ids:
                    if partial_line.max_date <= date_from:
                        line_amount += partial_line.amount
                for partial_line in line.matched_credit_ids:
                    if partial_line.max_date <= date_from:
                        line_amount -= partial_line.amount

                if not self.env.user.company_id.currency_id.is_zero(line_amount):
                    partners_amount[partner_id] += line_amount
                    lines[partner_id].append({
                        'line': line,
                        'amount': line_amount,
                        'period': i['label'],
                        })
            history.append(partners_amount)
            #history.append({'period': i, 'data' : partners_amount})
            #i['lines'].append(partners_amount)
        #need to reverese the order coz the report starts from right to left..
        history.reverse()
        # _logger.info('history is %s', history)
        # _logger.info('periods are %s', periods)
        for partner in partners:
            if partner['partner_id'] is None:
                partner['partner_id'] = False
            at_least_one_amount = False
            values = {}
            undue_amt = 0.0
            if partner['partner_id'] in undue_amounts:  # Making sure this partner actually was found by the query
                undue_amt = undue_amounts[partner['partner_id']]

            #total[6] = total[6] + undue_amt
            values['direction'] = undue_amt
            if not float_is_zero(values['direction'], precision_rounding=self.env.user.company_id.currency_id.rounding):
                at_least_one_amount = True

            #for p in periods:
                #i = p['index']
            for i in range(6):
                during = False
                if partner['partner_id'] in history[i]:
                    during = [history[i][partner['partner_id']]]
                #_logger.info('i is %s and during is %s', i, during)
                # Adding counter
                total[i] += (during and during[0] or 0)
                values[str(i)] = during and during[0] or 0.0
                if not float_is_zero(values[str(i)], precision_rounding=self.env.user.company_id.currency_id.rounding):
                    at_least_one_amount = True
            values['total'] = sum([values['direction']] + [values[str(i)] for i in range(6)])
            ## Add for total
            total[6] += values['total']
            values['partner_id'] = partner['partner_id']
            if partner['partner_id']:
                browsed_partner = self.env['res.partner'].browse(partner['partner_id'])
                max_name_length = 25
                values['name'] = browsed_partner.name and len(browsed_partner.name) >= max_name_length and browsed_partner.name[0:max_name_length] + '...' or browsed_partner.name
                values['trust'] = browsed_partner.trust
            else:
                values['name'] = _('Unknown Partner')
                values['trust'] = False

            if at_least_one_amount:
                res.append(values)
        return res, total, lines
    

    def get_dummy_lines(self):
        '''gets some dummy lines for testing purposes
        '''
        lines = [
            {
                'id' : 1,
                'name' : 'Darwesh',
                'level' : 2,
                'columns' : [
                    { 'name' : 'one'},
                    { 'name' : 'two'},
                    { 'name' : 'three'},
                    { 'name' : 'four'},
                    { 'name' : 'five'},
                    { 'name' : 'six'},
                    { 'name' : 'seven'},
                ],
                'unfoldable' : False,
            }
        ]
        return lines

    @api.model
    def get_lines(self, options, line_id=None):             
        sign = -1.0 if self.env.context.get('aged_balance') else 1.0
        lines = []
        periods = self.get_periods()
        account_types = [self.env.context.get('account_type')]
        results, total, amls = self.env['account.aged.partner.copia']._get_partner_move_lines(account_types, self._context['date_to'], 'posted')
        #_logger.info('results are %s\n\n', results)
        #_logger.info('total are %s\n\n', total)
        # _logger.info('lines are %s', amls)
        for values in results:
            #_logger.info('values are %s', values)
            if line_id and 'partner_%s' % (values['partner_id'],) != line_id:
                continue
            vals = {
                'id': 'partner_%s' % (values['partner_id'],),
                'name': values['name'],
                'level': 2,
                'columns': [{'name': self.format_value(sign * v)} for v in [values['0'], values['1'], \
                values['2'], values['3'], values['4'], values['5'], values['total']]],
                'trust': values['trust'],
                'unfoldable': True,
                'unfolded': 'partner_%s' % (values['partner_id'],) in options.get('unfolded_lines'),
            }
            #_logger.info('vals are %s', vals)
            lines.append(vals)
            if 'partner_%s' % (values['partner_id'],) in options.get('unfolded_lines'):
                for line in amls[values['partner_id']]:
                    #_logger.info('line is %s', line)
                    aml = line['line']
                    caret_type = 'account.move'
                    if aml.invoice_id:
                        caret_type = 'account.invoice.in' if aml.invoice_id.type in ('in_refund', 'in_invoice') else 'account.invoice.out'
                    elif aml.payment_id:
                        caret_type = 'account.payment'
                    vals = {
                        'id': aml.id,
                        'name': aml.move_id.name if aml.move_id.name else '/',
                        'caret_options': caret_type,
                        'level': 4,
                        'parent_id': 'partner_%s' % (values['partner_id'],),
                        'columns': [{'name': v} for v in [line['period'] == p['label'] and self.format_value(sign * line['amount']) or '' for p in periods]],
                    }
                    lines.append(vals)
                vals = {
                    'id': values['partner_id'],
                    'class': 'o_account_reports_domain_total',
                    'name': _('Total '),
                    'parent_id': 'partner_%s' % (values['partner_id'],),
                    'columns': [{'name': self.format_value(sign * v)} for v in [values['0'], values['1'], values['2'], values['3'], values['4'], values['5'], values['total']]],
                }
                lines.append(vals)
        if total and not line_id:
            total_line = {
                'id': 0,
                'name': _('Total'),
                'class': 'total',
                'level': 'None',
                'columns': [{'name': self.format_value(sign * v)} for v in [total[0], total[1], total[2], total[3], total[4], total[5], total[6]]],
            }
            lines.append(total_line)
        return lines
    
    def get_columns_name(self, options):
        columns = [{}]
        columns += [{'name': v, 'class': 'number'} for v in [l['label'] for l in self.get_periods()]]
        columns.append({'name' : 'Total', 'class' : 'number'})        
        return columns

class report_account_aged_partner_copia_payable(models.AbstractModel):
    _name = "account.aged.partner.copia.payable"
    _description = "Aged Partner Balances Copia - Payable"
    _inherit = 'account.aged.partner.copia'

    def set_context(self, options):
        ctx = super(report_account_aged_partner_copia_payable, self).set_context(options)
        ctx['account_type'] = 'payable'
        ctx['aged_balance'] = True
        return ctx

    def get_report_name(self):
        return _("Partner Ageing Report : Suppliers")


class ReportPartnerLedgerCopia(models.AbstractModel):
    _inherit = 'account.partner.ledger'

    @api.model
    def get_lines(self, options, line_id=None):
        '''
        Have to override this whole function just because of changing this line:
        unfold_all = context.get('print_mode') and not options.get('unfolded_lines') or options.get('partner_id')
        to this:
        unfold_all = context.get('unfold_all', False)
        Without this the Excel is created with lines unfolded always..
        '''
        lines = []
        if line_id:
            line_id = line_id.replace('partner_', '')
        context = self.env.context

        #If a default partner is set, we only want to load the line referring to it.
        if options.get('partner_id'):
            line_id = options['partner_id']

        grouped_partners = self.group_by_partner_id(options, line_id)
        sorted_partners = sorted(grouped_partners, key=lambda p: p.name or '')
        #unfold_all = context.get('print_mode') and not options.get('unfolded_lines') or options.get('partner_id')
        unfold_all = context.get('unfold_all', False)
        print('in copia_accounting..unfold_all is {}, options are {}, context is {}'.format(unfold_all, options, context or {}))
        total_initial_balance = total_debit = total_credit = total_balance = 0.0
        for partner in sorted_partners:
            debit = grouped_partners[partner]['debit']
            credit = grouped_partners[partner]['credit']
            balance = grouped_partners[partner]['balance']
            initial_balance = grouped_partners[partner]['initial_bal']['balance']
            total_initial_balance += initial_balance
            total_debit += debit
            total_credit += credit
            total_balance += balance
            lines.append({
                'id': 'partner_' + str(partner.id),
                'name': partner.name,
                'columns': [{'name': v} for v in [self.format_value(initial_balance), self.format_value(debit), self.format_value(credit), self.format_value(balance)]],
                'level': 2,
                'trust': partner.trust,
                'unfoldable': True,
                'unfolded': 'partner_' + str(partner.id) in options.get('unfolded_lines') or unfold_all,
                'colspan': 5,
            })
            print('--> {}'.format(lines))
            if 'partner_' + str(partner.id) in options.get('unfolded_lines') or unfold_all:
                progress = initial_balance
                domain_lines = []
                amls = grouped_partners[partner]['lines']
                too_many = False
                if len(amls) > 80 and not context.get('print_mode'):
                    amls = amls[-80:]
                    too_many = True
                for line in amls:
                    if options.get('cash_basis'):
                        line_debit = line.debit_cash_basis
                        line_credit = line.credit_cash_basis
                    else:
                        line_debit = line.debit
                        line_credit = line.credit
                    progress_before = progress
                    progress = progress + line_debit - line_credit
                    name = '-'.join(
                        (line.move_id.name not in ['', '/'] and [line.move_id.name] or []) +
                        (line.ref not in ['', '/', False] and [line.ref] or []) +
                        ([line.name] if line.name and line.name not in ['', '/'] else [])
                    )
                    if len(name) > 35 and not self.env.context.get('no_format'):
                        name = name[:32] + "..."
                    caret_type = 'account.move'
                    if line.invoice_id:
                        caret_type = 'account.invoice.in' if line.invoice_id.type in ('in_refund', 'in_invoice') else 'account.invoice.out'
                    elif line.payment_id:
                        caret_type = 'account.payment'
                    domain_lines.append({
                        'id': line.id,
                        'parent_id': 'partner_' + str(partner.id),
                        'name': line.date,
                        'columns': [{'name': v} for v in [line.journal_id.code, line.account_id.code, name, line.full_reconcile_id.name, self.format_value(progress_before),
                                    line_debit != 0 and self.format_value(line_debit) or '',
                                    line_credit != 0 and self.format_value(line_credit) or '',
                                    self.format_value(progress)]],
                        'caret_options': caret_type,
                        'level': 4,
                    })
                if too_many:
                    domain_lines.append({
                        'id': 'too_many_' + str(partner.id),
                        'parent_id': 'partner_' + str(partner.id),
                        'action': 'view_too_many',
                        'action_id': 'partner,%s' % (partner.id,),
                        'name': _('There are more than 80 items in this list, click here to see all of them'),
                        'colspan': 8,
                        'columns': [{}],
                    })
                lines += domain_lines
        if not line_id:
            lines.append({
                'id': 'grouped_partners_total',
                'name': _('Total'),
                'level': 0,
                'class': 'o_account_reports_domain_total',
                'columns': [{'name': v} for v in ['', '', '', '', self.format_value(total_initial_balance), self.format_value(total_debit), self.format_value(total_credit), self.format_value(total_balance)]],
            })
        return lines        