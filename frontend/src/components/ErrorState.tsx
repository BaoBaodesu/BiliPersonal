import { StateBlock, type StateProps } from './StateBlock'

export const ErrorState = (p: StateProps) => <StateBlock icon="error" {...p} />

export const OfflineState = (p: StateProps) => <StateBlock icon="offline" {...p} />
