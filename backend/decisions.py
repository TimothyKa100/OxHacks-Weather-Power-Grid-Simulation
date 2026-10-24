"""Decision support with explicit, user-editable illustrative financial assumptions."""
import math
import numpy as np

DEFAULT_ECONOMICS = {
    'cost_per_unit_gbp': 10000.,
    'outage_value_gbp_per_mwh': 1000.,
    'expected_events': 1.,
    'asset_costs_gbp': {},
}


def validate_economics(value=None):
    if value is None:
        return DEFAULT_ECONOMICS.copy()
    if not isinstance(value, dict) or set(value) - set(DEFAULT_ECONOMICS):
        raise ValueError('Unknown economics fields')
    result = DEFAULT_ECONOMICS.copy()
    for key, amount in value.items():
        if key == 'asset_costs_gbp':
            if not isinstance(amount,dict) or len(amount)>100:
                raise ValueError('asset_costs_gbp must be an asset-to-cost object')
            for asset, cost in amount.items():
                if not isinstance(asset,str) or isinstance(cost,bool) or not isinstance(cost,(int,float)) or not math.isfinite(cost) or not 0<=cost<=1e9:
                    raise ValueError('Asset costs must be finite nonnegative GBP values')
            result[key] = amount.copy()
            continue
        maximum = 1000 if key == 'expected_events' else 1e9
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or not 0 <= amount <= maximum:
            raise ValueError(f'{key} must be a finite number between 0 and {maximum}')
        result[key] = float(amount)
    return result


def cost_benefit(gain_mwh, units, assumptions, investment_gbp=None):
    cost = units * assumptions['cost_per_unit_gbp'] if investment_gbp is None else investment_gbp
    per_event = gain_mwh * assumptions['outage_value_gbp_per_mwh']
    benefit = per_event * assumptions['expected_events']
    return {
        'investment_gbp': cost,
        'avoided_loss_gbp_per_event': per_event,
        'avoided_loss_gbp_over_events': benefit,
        'net_benefit_gbp': benefit - cost,
        'benefit_cost_ratio': benefit / cost if cost > 0 else None,
        'break_even_events': cost / per_event if per_event > 0 and cost > 0 else None,
    }


def paired_evidence(before, after):
    gains = np.asarray(before) - np.asarray(after)
    mean = float(gains.mean())
    se = float(gains.std(ddof=1) / np.sqrt(len(gains)))
    return {
        'gain_ci95_mwh': [mean - 1.96*se, mean + 1.96*se],
        'fraction_scenarios_improved': float(np.mean(gains > 1e-6)),
        'fraction_scenarios_worse': float(np.mean(gains < -1e-6)),
        'interpretation': 'Approximate Monte Carlo confidence interval for mean gain; excludes weather/model uncertainty',
    }


def public_guidance():
    return {
        'scope': 'General outage preparedness; this schematic model cannot predict an individual home outage or a safe walking route.',
        'actions': [
            {'title': 'Prepare before disruption', 'detail': 'Charge phones and power banks; keep a torch, essential contacts and necessary supplies accessible.'},
            {'title': 'Plan for essential support', 'detail': 'If you depend on powered medical equipment, arrange a backup plan with your care provider and check eligibility for your network operator’s Priority Services Register.'},
            {'title': 'Check official updates', 'detail': 'Follow your local network operator and Met Office warnings. In Great Britain, call 105 to report a power cut.'},
            {'title': 'Keep clear of damaged infrastructure', 'detail': 'Keep away from fallen wires and damaged electrical equipment; call 999 for immediate danger. Never approach equipment to inspect or reinforce it.'},
        ],
        'sources': [
            {'label': 'Power Cut 105', 'url': 'https://www.powercut105.com/'},
            {'label': 'Met Office warnings', 'url': 'https://weather.metoffice.gov.uk/warnings-and-advice/uk-warnings'},
        ],
    }
