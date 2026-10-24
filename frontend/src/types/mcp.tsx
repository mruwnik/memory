export type CollectionMetadata = {
    schema: Record<string, SchemaArg>
    filters: Record<string, SchemaArg>
    size: number
}

export type SchemaArg = {
    type: string
    description: string
}