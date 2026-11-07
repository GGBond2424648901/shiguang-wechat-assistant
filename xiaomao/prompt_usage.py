"""Memory-only provider usage evidence; never retains prompts, replies, or keys."""
from copy import deepcopy
import threading


_FIELDS = ('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens')


def _count(value):
    return value if type(value) is int and 0 <= value <= 10**10 else None


class PromptUsage:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = dict(requests=0, usage_reported_requests=0, cache_reported_requests=0,
                         **{field:0 for field in _FIELDS})
        self.last = {}

    def record(self, response):
        usage = response.get('usage') if isinstance(response,dict) else None
        usage = usage if isinstance(usage,dict) else {}
        values = {field:_count(usage.get(field)) for field in _FIELDS}
        prompt, hit, miss = (values[key] for key in ('prompt_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens'))
        cached = all(value is not None for value in (prompt, hit, miss)) and hit+miss == prompt
        with self.lock:
            self.data['requests'] += 1
            self.data['usage_reported_requests'] += int(any(values[key] is not None for key in _FIELDS[:3]))
            self.data['cache_reported_requests'] += int(cached)
            self.last = dict(cache_reported=cached, evidence='provider_usage' if cached else 'not_reported')
            for field, value in values.items():
                if field.startswith('prompt_cache_') and not cached:
                    continue
                if value is not None:
                    self.data[field] += value
                    self.last[field] = value

    def snapshot(self):
        with self.lock:
            result = dict(self.data, last=deepcopy(self.last))
        denominator = result['prompt_cache_hit_tokens']+result['prompt_cache_miss_tokens']
        result['cache_hit_ratio'] = result['prompt_cache_hit_tokens']/denominator if denominator else None
        return result
