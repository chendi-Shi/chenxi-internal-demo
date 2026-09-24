import { readFile, writeFile } from 'node:fs/promises';

const openapiPath = 'docs/contracts/openapi.json';
const examplesPath = 'docs/contracts/examples.json';
const outputPath = 'src/api/generated/contracts.ts';

const openapi = JSON.parse(await readFile(openapiPath, 'utf8'));
const examples = JSON.parse(await readFile(examplesPath, 'utf8'));
const schemas = openapi.components?.schemas;

if (!schemas || typeof schemas !== 'object') {
  throw new Error('OpenAPI components.schemas is missing');
}

function refName(ref) {
  const prefix = '#/components/schemas/';
  if (!ref.startsWith(prefix)) throw new Error(`Unsupported ref: ${ref}`);
  return ref.slice(prefix.length);
}

function literal(value) {
  return JSON.stringify(value);
}

function propertyName(name) {
  return /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(name) ? name : JSON.stringify(name);
}

function typeFromSchema(schema) {
  if (!schema || typeof schema !== 'object') return 'unknown';
  if (schema.$ref) return refName(schema.$ref);
  if (schema.const !== undefined) return literal(schema.const);
  if (Array.isArray(schema.enum)) return schema.enum.map(literal).join(' | ');
  if (Array.isArray(schema.anyOf)) return schema.anyOf.map(typeFromSchema).join(' | ');

  switch (schema.type) {
    case 'string':
      return 'string';
    case 'number':
    case 'integer':
      return 'number';
    case 'boolean':
      return 'boolean';
    case 'null':
      return 'null';
    case 'array': {
      if (Array.isArray(schema.prefixItems)) {
        return `[${schema.prefixItems.map(typeFromSchema).join(', ')}]`;
      }
      return `Array<${typeFromSchema(schema.items)}>`;
    }
    case 'object': {
      const properties = schema.properties ?? {};
      const required = new Set(schema.required ?? []);
      const entries = Object.entries(properties).map(([name, value]) => {
        const optional = required.has(name) ? '' : '?';
        return `${propertyName(name)}${optional}: ${typeFromSchema(value)}`;
      });

      let recordType = '';
      const patternSchema = Object.values(schema.patternProperties ?? {})[0];
      if (patternSchema) recordType = `Record<string, ${typeFromSchema(patternSchema)}>`;
      else if (schema.additionalProperties && typeof schema.additionalProperties === 'object') {
        recordType = `Record<string, ${typeFromSchema(schema.additionalProperties)}>`;
      }

      if (entries.length === 0) return recordType || 'Record<string, unknown>';
      const objectType = `{ ${entries.join('; ')} }`;
      return recordType ? `${objectType} & ${recordType}` : objectType;
    }
    default:
      return 'unknown';
  }
}

function declaration(name, schema) {
  if (schema.type === 'object' && schema.properties && !schema.patternProperties) {
    const required = new Set(schema.required ?? []);
    const fields = Object.entries(schema.properties).map(([field, value]) => {
      const optional = required.has(field) ? '' : '?';
      return `  ${propertyName(field)}${optional}: ${typeFromSchema(value)};`;
    });
    return `export interface ${name} {\n${fields.join('\n')}\n}`;
  }
  return `export type ${name} = ${typeFromSchema(schema)};`;
}

const schemaNames = Object.keys(schemas).sort();
const declarations = schemaNames.map((name) => declaration(name, schemas[name])).join('\n\n');
const typeMap = schemaNames.map((name) => `  ${name}: ${name};`).join('\n');

const generated = `// Generated from docs/contracts/openapi.json and examples.json. Do not edit.\n\n${declarations}\n\nexport interface ContractTypeMap {\n${typeMap}\n}\n\nexport type ContractSchemaName = keyof ContractTypeMap;\n\nexport const contractSchemas = ${JSON.stringify(schemas, null, 2)} as const;\n\nexport const contractExamples = ${JSON.stringify(examples, null, 2)} as const;\n`;

if (process.argv.includes('--check')) {
  const existing = await readFile(outputPath, 'utf8').catch(() => '');
  if (existing !== generated) {
    throw new Error(`${outputPath} is stale; run npm run generate:contracts`);
  }
  console.log(`Generated contract is current: ${outputPath}`);
} else {
  await writeFile(outputPath, generated, 'utf8');
  console.log(`Generated ${outputPath} from OpenAPI ${openapi.info?.version ?? 'unknown'}.`);
}
