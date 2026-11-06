import type {Json} from './model';

/** Discard only Chaldea's all-skipped random-effect simulation queue on import. */
export function prepareImportedBattle(data:Json):Json {
 const delegate=data.delegate;
 if(!delegate||typeof delegate!=='object'||Array.isArray(delegate))return data;
 const choices=delegate.actWeightSelections;
 // Mixed queues must remain intact: filtering entries would shift their meaning.
 if(!Array.isArray(choices)||!choices.length||!choices.every(value=>value===-1))return data;
 const imported=structuredClone(data);
 delete imported.delegate.actWeightSelections;
 if(!Object.keys(imported.delegate).length)delete imported.delegate;
 return imported;
}
