import {
  contractSchemas,
  type ContractSchemaName,
  type ContractTypeMap,
} from './generated/contracts.ts';

type JsonObject = Record<string, unknown>;

export class ContractValidationError extends Error {
  readonly path: string;

  constructor(path: string, message: string) {
    super(`${path}: ${message}`);
    this.name = 'ContractValidationError';
    this.path = path;
  }
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asSchema(value: unknown, path: string): JsonObject {
  if (!isObject(value)) throw new ContractValidationError(path, 'invalid schema');
  return value;
}

function fail(path: string, message: string): never {
  throw new ContractValidationError(path, message);
}

function validate(schemaValue: unknown, value: unknown, path: string): void {
  const schema = asSchema(schemaValue, path);

  if (typeof schema.$ref === 'string') {
    const name = schema.$ref.replace('#/components/schemas/', '') as ContractSchemaName;
    const referenced = contractSchemas[name];
    if (!referenced) fail(path, `unknown schema reference ${schema.$ref}`);
    validate(referenced, value, path);
    return;
  }

  if (Array.isArray(schema.anyOf)) {
    const matched = schema.anyOf.some((candidate) => {
      try {
        validate(candidate, value, path);
        return true;
      } catch (error) {
        if (error instanceof ContractValidationError) return false;
        throw error;
      }
    });
    if (!matched) fail(path, 'does not match any allowed shape');
    return;
  }

  if (Object.hasOwn(schema, 'const') && value !== schema.const) {
    fail(path, `must equal ${JSON.stringify(schema.const)}`);
  }

  if (Array.isArray(schema.enum) && !schema.enum.includes(value)) {
    fail(path, `must be one of ${schema.enum.map(String).join(', ')}`);
  }

  switch (schema.type) {
    case 'null':
      if (value !== null) fail(path, 'must be null');
      return;
    case 'string': {
      if (typeof value !== 'string') fail(path, 'must be a string');
      const minLength = typeof schema.minLength === 'number' ? schema.minLength : undefined;
      const maxLength = typeof schema.maxLength === 'number' ? schema.maxLength : undefined;
      if (minLength !== undefined && value.length < minLength) fail(path, `must have at least ${minLength} characters`);
      if (maxLength !== undefined && value.length > maxLength) fail(path, `must have at most ${maxLength} characters`);
      if (typeof schema.pattern === 'string' && !new RegExp(schema.pattern).test(value)) {
        fail(path, `must match ${schema.pattern}`);
      }
      return;
    }
    case 'number':
    case 'integer': {
      if (typeof value !== 'number' || !Number.isFinite(value)) fail(path, 'must be a finite number');
      if (schema.type === 'integer' && !Number.isInteger(value)) fail(path, 'must be an integer');
      if (typeof schema.minimum === 'number' && value < schema.minimum) fail(path, `must be >= ${schema.minimum}`);
      if (typeof schema.maximum === 'number' && value > schema.maximum) fail(path, `must be <= ${schema.maximum}`);
      return;
    }
    case 'boolean':
      if (typeof value !== 'boolean') fail(path, 'must be a boolean');
      return;
    case 'array': {
      if (!Array.isArray(value)) fail(path, 'must be an array');
      if (typeof schema.minItems === 'number' && value.length < schema.minItems) fail(path, `must contain at least ${schema.minItems} items`);
      if (typeof schema.maxItems === 'number' && value.length > schema.maxItems) fail(path, `must contain at most ${schema.maxItems} items`);
      if (Array.isArray(schema.prefixItems)) {
        schema.prefixItems.forEach((itemSchema, index) => validate(itemSchema, value[index], `${path}[${index}]`));
      } else if (schema.items) {
        value.forEach((item, index) => validate(schema.items, item, `${path}[${index}]`));
      }
      return;
    }
    case 'object': {
      if (!isObject(value)) fail(path, 'must be an object');
      const properties = isObject(schema.properties) ? schema.properties : {};
      const required = Array.isArray(schema.required) ? schema.required : [];
      const patterns = isObject(schema.patternProperties) ? schema.patternProperties : {};

      if (typeof schema.maxProperties === 'number' && Object.keys(value).length > schema.maxProperties) {
        fail(path, `must contain at most ${schema.maxProperties} properties`);
      }

      for (const field of required) {
        if (typeof field === 'string' && !Object.hasOwn(value, field)) fail(`${path}.${field}`, 'is required');
      }

      for (const [field, fieldValue] of Object.entries(value)) {
        if (Object.hasOwn(properties, field)) {
          validate(properties[field], fieldValue, `${path}.${field}`);
          continue;
        }

        const matchingPatterns = Object.entries(patterns).filter(([pattern]) => new RegExp(pattern).test(field));
        if (matchingPatterns.length > 0) {
          matchingPatterns.forEach(([, patternSchema]) => validate(patternSchema, fieldValue, `${path}.${field}`));
          continue;
        }

        if (schema.additionalProperties === false) fail(`${path}.${field}`, 'is not allowed');
        if (isObject(schema.additionalProperties)) validate(schema.additionalProperties, fieldValue, `${path}.${field}`);
      }
      return;
    }
    default:
      return;
  }
}

export function parseContract<Name extends ContractSchemaName>(
  schemaName: Name,
  value: unknown,
): ContractTypeMap[Name] {
  validate(contractSchemas[schemaName], value, schemaName);
  return value as ContractTypeMap[Name];
}
