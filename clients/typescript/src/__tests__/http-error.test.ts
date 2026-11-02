import { HttpClient, HttpError, isHttpError } from '../index';
import * as httpClientModule from '../client/http-client';

const errorWithBody = (status: number, body: unknown) => new HttpError(status, 'Status', body);

describe('HttpError.detail', () => {
  it('returns a string detail as is', () => {
    expect(errorWithBody(404, { detail: "Profile 'missing' not found" }).detail).toBe(
      "Profile 'missing' not found"
    );
  });

  it('ignores the extra fields the server sends next to a string detail', () => {
    expect(
      errorWithBody(409, { detail: 'Credential binding required', retryable: true }).detail
    ).toBe('Credential binding required');
    expect(
      errorWithBody(500, {
        detail: 'Internal Server Error',
        exception: 'boom',
        error_id: '0123abcd',
      }).detail
    ).toBe('Internal Server Error');
  });

  it('returns the msg of a single validation error without a field prefix', () => {
    const error = errorWithBody(422, {
      detail: [
        {
          type: 'string_too_long',
          loc: ['body', 'name'],
          msg: 'String should have at most 128 characters',
          input: 'x'.repeat(133),
          ctx: { max_length: 128 },
        },
      ],
    });

    expect(error.detail).toBe('String should have at most 128 characters');
  });

  it('joins several validation errors, each prefixed with its field name', () => {
    const error = errorWithBody(422, {
      detail: [
        { type: 'missing', loc: ['body', 'agent', 'llm', 'model'], msg: 'Field required' },
        { type: 'int_parsing', loc: ['query', 'limit'], msg: 'Input should be a valid integer' },
        { type: 'missing', loc: ['body', 'servers', 0, 'name'], msg: 'Field required' },
      ],
    });

    expect(error.detail).toBe(
      'model: Field required; limit: Input should be a valid integer; name: Field required'
    );
  });

  it('leaves a validation msg unprefixed when its loc names no field', () => {
    const error = errorWithBody(422, {
      detail: [
        { type: 'dict_type', loc: [], msg: 'Input should be a valid dictionary' },
        { type: 'missing', loc: ['body', 'name'], msg: 'Field required' },
      ],
    });

    expect(error.detail).toBe('Input should be a valid dictionary; name: Field required');
  });

  it('skips validation entries that are malformed or have a blank msg', () => {
    const error = errorWithBody(422, {
      detail: [
        { type: 'missing', loc: ['body', 'name'], msg: '' },
        { loc: ['body', 'model'], msg: 'no type' },
        { type: 'missing', loc: 'body.model', msg: 'loc is not an array' },
        'not an object',
        null,
        { type: 'missing', loc: ['body', 'model'], msg: 'Field required' },
      ],
    });

    expect(error.detail).toBe('Field required');
  });

  it('returns undefined for a validation array with no usable entry', () => {
    expect(errorWithBody(422, { detail: [] }).detail).toBeUndefined();
    expect(errorWithBody(422, { detail: [{ msg: 'no loc or type' }] }).detail).toBeUndefined();
  });

  it('returns the message of an object detail (launch errors)', () => {
    const error = errorWithBody(422, {
      detail: {
        code: 'unresolved_profile_references',
        message: "LLM profile 'gone' not found",
        dangling_llm_profile_ref: 'gone',
        dangling_mcp_server_refs: [],
      },
    });

    expect(error.detail).toBe("LLM profile 'gone' not found");
  });

  it('returns undefined for a non-JSON body', () => {
    expect(errorWithBody(502, '<html><body>502 Bad Gateway</body></html>').detail).toBeUndefined();
  });

  it('returns undefined for an empty or missing body', () => {
    expect(errorWithBody(500, null).detail).toBeUndefined();
    expect(errorWithBody(500, '').detail).toBeUndefined();
    expect(new HttpError(500, 'Internal Server Error').detail).toBeUndefined();
  });

  it.each([
    ['a top-level error field', { error: 'database unavailable' }],
    ['a top-level message field', { message: 'Runner quota exceeded' }],
    ['a null detail', { detail: null }],
    ['a numeric detail', { detail: 42 }],
    ['a blank string detail', { detail: '   ' }],
    ['an object detail without a message', { detail: { code: 'unknown' } }],
    ['an object detail with a blank message', { detail: { code: 'x', message: '' } }],
    ['a JSON array body', [{ msg: 'Field required' }]],
  ])('returns undefined for %s', (_label, body) => {
    expect(errorWithBody(400, body).detail).toBeUndefined();
  });

  it('keeps the transport message and adds detail when HttpClient throws', async () => {
    const body = {
      detail: [{ type: 'missing', loc: ['body', 'name'], msg: 'Field required', input: {} }],
    };
    const originalFetch = global.fetch;
    global.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status: 422,
          statusText: 'Unprocessable Entity',
          headers: { 'content-type': 'application/json' },
        })
    ) as typeof fetch;

    try {
      const client = new HttpClient({ baseUrl: 'http://example.com' });
      const error = await client.post('/api/profiles', {}).catch((e: unknown) => e);

      expect(isHttpError(error)).toBe(true);
      expect((error as HttpError).message).toBe(
        `HTTP request failed (422 Unprocessable Entity): ${JSON.stringify(body)}`
      );
      expect((error as HttpError).detail).toBe('Field required');
    } finally {
      global.fetch = originalFetch;
    }
  });
});

describe('HttpError.validationErrors', () => {
  it('returns loc, msg and type of each entry, without input or ctx', () => {
    const error = errorWithBody(422, {
      detail: [
        {
          type: 'string_too_long',
          loc: ['body', 'name'],
          msg: 'String should have at most 128 characters',
          input: 'secret-ish',
          ctx: { max_length: 128 },
        },
        { type: 'missing', loc: ['body', 'servers', 0, 'name'], msg: 'Field required' },
        { msg: 'malformed' },
      ],
    });

    expect(error.validationErrors).toEqual([
      {
        type: 'string_too_long',
        loc: ['body', 'name'],
        msg: 'String should have at most 128 characters',
      },
      { type: 'missing', loc: ['body', 'servers', 0, 'name'], msg: 'Field required' },
    ]);
  });

  it('returns undefined when the body is not a validation array', () => {
    expect(errorWithBody(404, { detail: 'Not Found' }).validationErrors).toBeUndefined();
    expect(errorWithBody(422, { detail: [] }).validationErrors).toBeUndefined();
    expect(errorWithBody(502, 'Bad Gateway').validationErrors).toBeUndefined();
    expect(new HttpError(500, 'Internal Server Error').validationErrors).toBeUndefined();
  });
});

describe('isHttpError', () => {
  it('recognizes HttpError instances and subclasses', () => {
    class CustomHttpError extends HttpError {}

    expect(isHttpError(new HttpError(404, 'Not Found'))).toBe(true);
    expect(isHttpError(new CustomHttpError(409, 'Conflict'))).toBe(true);
  });

  it('is the same function on the package root and the http-client entry point', () => {
    expect(isHttpError).toBe(httpClientModule.isHttpError);
    expect(HttpError).toBe(httpClientModule.HttpError);
  });

  it('recognizes an HttpError created by a second copy of the module', async () => {
    vi.resetModules();
    const secondCopy = await import('../client/http-client');
    const foreign = new secondCopy.HttpError(404, 'Not Found', { detail: 'Not Found' });

    expect(secondCopy.HttpError).not.toBe(HttpError);
    expect(foreign).not.toBeInstanceOf(HttpError);
    expect(isHttpError(foreign)).toBe(true);
    expect(secondCopy.isHttpError(new HttpError(404, 'Not Found'))).toBe(true);
    if (isHttpError(foreign)) {
      expect(foreign.status).toBe(404);
      expect(foreign.detail).toBe('Not Found');
    }
  });

  it('recognizes an object carrying the registry brand that this class did not create', () => {
    const lookalike = Object.assign(new Error('HTTP request failed (404 Not Found)'), {
      status: 404,
      statusText: 'Not Found',
      response: { detail: 'Not Found' },
    });
    Object.defineProperty(lookalike, Symbol.for('@openhands/typescript-client/HttpError'), {
      value: true,
    });

    expect(lookalike).not.toBeInstanceOf(HttpError);
    expect(isHttpError(lookalike)).toBe(true);
  });

  it('rejects look-alikes without the brand and non-errors', () => {
    const namedOnly = Object.assign(new Error('HTTP 404'), { name: 'HttpError', status: 404 });
    const wrongBrand = {};
    Object.defineProperty(wrongBrand, Symbol.for('@openhands/typescript-client/HttpError'), {
      value: 'yes',
    });

    expect(isHttpError(namedOnly)).toBe(false);
    expect(isHttpError(wrongBrand)).toBe(false);
    expect(isHttpError({ status: 404, response: { detail: 'Not Found' } })).toBe(false);
    expect(isHttpError(new Error('HTTP 404'))).toBe(false);
    expect(isHttpError(null)).toBe(false);
    expect(isHttpError(undefined)).toBe(false);
    expect(isHttpError('HttpError')).toBe(false);
  });

  it('keeps the brand non-enumerable, so a spread copy is not an HttpError', () => {
    const error = new HttpError(404, 'Not Found', { detail: 'Not Found' });
    const brand = Symbol.for('@openhands/typescript-client/HttpError');

    expect(Object.prototype.propertyIsEnumerable.call(error, brand)).toBe(false);
    expect(Object.keys(error).sort()).toEqual(['name', 'response', 'status', 'statusText']);
    expect(isHttpError({ ...error })).toBe(false);
  });
});
