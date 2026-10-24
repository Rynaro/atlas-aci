(function_item name: (identifier) @name) @def.function
(function_signature_item name: (identifier) @name) @def.signature
(struct_item name: (type_identifier) @name) @def.struct
(enum_item name: (type_identifier) @name) @def.enum
(trait_item name: (type_identifier) @name) @def.trait
(mod_item name: (identifier) @name) @def.module
(call_expression function: (_) @callee) @call